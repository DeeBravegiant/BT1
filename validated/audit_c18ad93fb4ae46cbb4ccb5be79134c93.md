### Title
`verify_foreign_transaction` Caller-Agnostic Request Key and Absence of Completed-Request Tracking Enable Cross-Chain Replay and Double-Spend - (File: `crates/contract/src/lib.rs`, `crates/contract/src/pending_requests.rs`, `crates/near-mpc-contract-interface/src/types/foreign_chain.rs`)

---

### Summary

The `verify_foreign_transaction` endpoint uses a caller-agnostic request key (no predecessor account ID), stores only *pending* requests, and removes entries upon resolution. There is no completed-verification registry. The signed payload (`ForeignTxSignPayload`) contains no nonce, timestamp, or caller identity. Together, these properties allow: (1) any unprivileged caller to piggyback on a pending request and receive the same MPC-signed attestation, and (2) the same foreign transaction to be re-verified indefinitely, producing fresh valid signatures over the same `payload_hash`. Bridge contracts that do not independently track consumed attestations are exposed to double-spend conditions.

---

### Finding Description

**Caller-agnostic request key.** `VerifyForeignTransactionRequest` is defined as:

```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
``` [1](#0-0) 

No predecessor/caller account ID is included. The conversion function `args_into_verify_foreign_tx_request` confirms this — it simply copies the three fields and discards the caller context:

```rust
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
    }
}
``` [2](#0-1) 

This is in direct contrast to `sign()` and `request_app_private_key()`, which both bind the caller's `predecessor` into the request key via `SignatureRequest::new(&predecessor, ...)` and `CKDRequest::new(&predecessor, ...)`. [3](#0-2) 

**No completed-request tracking.** The pending map is the only state:

```rust
pending_verify_foreign_tx_requests: LookupMap<VerifyForeignTransactionRequest, Vec<YieldIndex>>,
``` [4](#0-3) 

`resolve_yields_for` removes the entry on success:

```rust
let resumed = requests
    .remove(request)   // entry is gone after respond
    ...
``` [5](#0-4) 

Once removed, an identical `VerifyForeignTransactionRequestArgs` can be submitted again immediately. The contract has no memory of having already attested to that foreign transaction.

**No replay-prevention in the signed payload.** The payload that MPC nodes sign is:

```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}
// msg_hash = SHA-256(borsh(ForeignTxSignPayload))
``` [6](#0-5) 

There is no nonce, block height, timestamp, or caller identity. For a given foreign transaction with deterministic extracted values, `payload_hash` is always the same value. The contract test comment confirms this explicitly: *"simulate signature with the root key (no tweak for foreign tx)"*. [7](#0-6) 

**Signature verified against root key only.** `respond_verify_foreign_tx` verifies the signature against the undifferentiated root public key — no per-caller tweak is applied:

```rust
// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,   // root key, no tweak
)
``` [8](#0-7) 

This means every valid `VerifyForeignTransactionResponse` for the same foreign transaction is interchangeable — any holder of any such response can present it to a bridge contract.

**Fan-out delivers the same attestation to all callers.** The codebase explicitly tests and documents that different callers submitting the same request all receive the same response: [9](#0-8) 

---

### Impact Explanation

Two concrete attack paths arise:

**Path 1 — Piggyback attestation acquisition.** Alice submits `verify_foreign_transaction(bitcoin_tx_X)` to initiate a bridge deposit. Bob (attacker) observes the pending request on-chain and submits the identical request. Both are queued under the same caller-agnostic key. When MPC nodes respond, Bob receives the same `VerifyForeignTransactionResponse` as Alice. Bob can now present this attestation to any bridge contract that does not bind the attestation to the original submitter, claiming Alice's bridge deposit before she does.

**Path 2 — Re-verification replay.** Alice obtains `(payload_hash_X, sig_X1)` and uses it to claim 1 BTC on a bridge. She then re-submits `verify_foreign_transaction(bitcoin_tx_X)`. The contract accepts it (no completed-request guard), MPC nodes sign again, and Alice receives `(payload_hash_X, sig_X2)`. Both `sig_X1` and `sig_X2` are valid ECDSA signatures over the same `payload_hash_X` under the root key. A bridge contract that tracks only used signatures (not `payload_hash` values) would accept `sig_X2` as a fresh attestation, enabling a double-spend.

The impact class is **High**: cross-chain replay and double-spend conditions in bridge contracts that rely on MPC attestations as the sole proof of foreign-chain finality.

---

### Likelihood Explanation

The MPC contract is explicitly positioned as a "trusted relayer" replacement — *"so NEAR contracts can react to external chain events without a trusted relayer."* A bridge contract developer reading the contract API documentation would see that `verify_foreign_transaction` verifies a foreign transaction and returns a signed attestation, with no mention of replay-protection obligations on the bridge side. The README documents the fan-out behavior but does not warn that the same attestation can be obtained by any caller or re-obtained after resolution. Developers building on this API are likely to treat the MPC attestation as a self-contained proof, without implementing independent `payload_hash` deduplication. Likelihood is **Medium**. [10](#0-9) 

---

### Recommendation

1. **Document replay-protection obligations explicitly.** State in the contract README and `verify_foreign_transaction` docstring that bridge contracts must maintain a set of consumed `payload_hash` values and reject any attestation whose `payload_hash` has been seen before.

2. **Include caller identity in the request key.** Mirror the pattern used by `sign()` and `request_app_private_key()`: derive the `VerifyForeignTransactionRequest` key from `(predecessor, request, domain_id, payload_version)`. This prevents Bob from piggybacking on Alice's pending request.

3. **Add a completed-verification registry.** After `respond_verify_foreign_tx` resolves a request, record the `payload_hash` in a persistent set. Reject new `verify_foreign_transaction` submissions whose computed `payload_hash` is already in this set.

4. **Embed a nonce or block-height in the signed payload.** Including `env::block_height()` or a caller-supplied nonce in `ForeignTxSignPayloadV1` would make each attestation unique, preventing signature reuse even if the same foreign transaction is re-verified.

---

### Proof of Concept

**Piggyback (Path 1):**
```
1. Alice calls verify_foreign_transaction({bitcoin_tx_X, domain_id, V1})
   → pending_verify_foreign_tx_requests[{bitcoin_tx_X, domain_id, V1}] = [yield_alice]

2. Bob calls verify_foreign_transaction({bitcoin_tx_X, domain_id, V1})
   → pending_verify_foreign_tx_requests[{bitcoin_tx_X, domain_id, V1}] = [yield_alice, yield_bob]

3. MPC node calls respond_verify_foreign_tx({bitcoin_tx_X, domain_id, V1}, response)
   → resolve_yields_for drains both yields with the same response bytes
   → Alice receives VerifyForeignTransactionResponse{payload_hash_X, sig_X}
   → Bob receives VerifyForeignTransactionResponse{payload_hash_X, sig_X}  ← same attestation

4. Bob presents {payload_hash_X, sig_X} to bridge contract before Alice.
   Bridge contract verifies sig_X against MPC root key → valid.
   Bob claims Alice's bridge deposit.
```

**Re-verification replay (Path 2):**
```
1. Alice calls verify_foreign_transaction({bitcoin_tx_X, ...}) → gets {payload_hash_X, sig_X1}
2. Alice uses {payload_hash_X, sig_X1} to claim 1 BTC on bridge.
3. Alice calls verify_foreign_transaction({bitcoin_tx_X, ...}) again.
   Contract accepts (no completed-request guard).
   → gets {payload_hash_X, sig_X2}  (different ECDSA signature, same payload_hash)
4. Alice presents {payload_hash_X, sig_X2} to bridge.
   Bridge checks sig_X2 ≠ sig_X1 → not in "used signatures" set → accepts.
   Alice claims another 1 BTC. Double-spend complete.
```

The root cause lines are:
- Caller-agnostic key construction: [2](#0-1) 
- No completed-request guard: [11](#0-10) 
- No replay field in signed payload: [6](#0-5) 
- Root-key-only signature verification: [8](#0-7)

### Citations

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
}
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1499-1509)
```rust
pub struct ForeignTxSignPayloadV1 {
    pub request: ForeignChainRpcRequest,
    pub values: Vec<ExtractedValue>,
}

impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
        let mut hasher = sha2::Sha256::new();
        borsh::BorshSerialize::serialize(self, &mut hasher)?;
        Ok(Hash256(hasher.finalize().into()))
    }
```

**File:** crates/contract/src/dto_mapping.rs (L840-848)
```rust
pub fn args_into_verify_foreign_tx_request(
    args: dtos::VerifyForeignTransactionRequestArgs,
) -> dtos::VerifyForeignTransactionRequest {
    dtos::VerifyForeignTransactionRequest {
        domain_id: args.domain_id,
        request: args.request,
        payload_version: args.payload_version,
    }
}
```

**File:** crates/contract/src/lib.rs (L155-155)
```rust
    pending_verify_foreign_tx_requests: LookupMap<VerifyForeignTransactionRequest, Vec<YieldIndex>>,
```

**File:** crates/contract/src/lib.rs (L379-384)
```rust
        let request = SignatureRequest::new(
            request.domain_id,
            request.payload,
            &predecessor,
            &request.path,
        );
```

**File:** crates/contract/src/lib.rs (L728-734)
```rust
                // Check the signature is correct against the root public key
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    &payload_hash,
                    &secp_pk,
                )
                .is_ok()
```

**File:** crates/contract/src/lib.rs (L3242-3255)
```rust
        // And: caller bob submits the identical request — a different account would today
        // be blocked from receiving a response by alice's submission.
        let bob = AccountId::from_str("bob.near").unwrap();
        testing_env!(
            VMContextBuilder::new()
                .signer_account_id(bob.clone())
                .predecessor_account_id(bob)
                .current_account_id(context.current_account_id.clone())
                .attached_deposit(NearToken::from_yoctonear(1))
                .build()
        );
        contract.verify_foreign_transaction(request_args);

        // Then: both yields are queued under the single (caller-agnostic) request key.
```

**File:** crates/contract/src/lib.rs (L3694-3698)
```rust
        // simulate signature with the root key (no tweak for foreign tx)
        let secret_key_ec: elliptic_curve::SecretKey<Secp256k1> =
            elliptic_curve::SecretKey::from_bytes(&secret_key.to_bytes()).unwrap();
        let secret_key = SigningKey::from_bytes(&secret_key_ec.to_bytes()).unwrap();
        let (signature, recovery_id) = secret_key.sign_prehash_recoverable(&payload_hash).unwrap();
```

**File:** crates/contract/src/pending_requests.rs (L74-88)
```rust
    let resumed = requests
        .remove(request)
        .unwrap_or_default()
        .into_iter()
        .map(|YieldIndex { data_id }| {
            env::promise_yield_resume(&data_id, response_bytes.clone());
        })
        .count();

    if resumed > 0 {
        Ok(())
    } else {
        Err(InvalidParameters::RequestNotFound.into())
    }
}
```

**File:** crates/contract/README.md (L264-264)
```markdown
| `verify_foreign_transaction(request: VerifyForeignTransactionRequestArgs)`                   | Submits a foreign-chain transaction verification request to the contract. Requires a deposit of 1 yoctonear and that the requested foreign chain is in the contract's supported set. Duplicate submissions of the same request (same caller, domain, chain, and payload) while an earlier one is still pending are queued and all receive the same response when the MPC nodes reply; the queue is bounded — concurrent duplicates beyond that bound are rejected with `PendingRequestQueueFull`. | deferred to promise        | `10 Tgas`       | `~7 Tgas`          |
```
