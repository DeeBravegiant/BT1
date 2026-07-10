### Title
`respond_verify_foreign_tx` Does Not Bind `payload_hash` to `request` — Single-Node Forged Foreign-Chain Verification Attestation - (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_verify_foreign_tx` function verifies only that the submitted signature is cryptographically valid over the caller-supplied `response.payload_hash` using the root public key. It never checks that `response.payload_hash` is correctly derived from `request.request` (the actual foreign-chain transaction data). A single TEE-attested MPC node can therefore replay a valid `{payload_hash, signature}` pair from any prior foreign-chain verification response against a *different* pending request, causing the contract to resolve that request with a false attestation — without any threshold participation.

---

### Finding Description

**`verify_foreign_transaction` stores no caller binding and no entropy in the request key.**

`verify_foreign_transaction` calls `check_request_preconditions` but discards the returned `(domain_config, predecessor)` tuple entirely: [1](#0-0) 

The resulting `VerifyForeignTransactionRequest` stored in `pending_verify_foreign_tx_requests` contains only `{request, domain_id, payload_version}` — no caller account, no block entropy: [2](#0-1) 

The conversion function `args_into_verify_foreign_tx_request` confirms that the predecessor is simply dropped: [3](#0-2) 

**`respond_verify_foreign_tx` verifies the signature but not the payload hash binding.**

The function verifies `response.signature` over `response.payload_hash` against the root public key, then immediately resolves the pending yield: [4](#0-3) 

There is no check that `response.payload_hash` is the hash that would be expected from `request.request`. The node-side `VerifyForeignTxRequest` carries `entropy` from the block context: [5](#0-4) 

but that entropy is never stored in the on-chain `VerifyForeignTransactionRequest`, so the contract has no way to recompute the expected payload hash — and makes no attempt to do so.

**Contrast with `respond` for regular signatures.**

`respond` derives the expected public key from `request.tweak` (which encodes the caller's account and derivation path) and verifies the signature against that derived key: [6](#0-5) 

`respond_verify_foreign_tx` uses the raw root key and accepts any `payload_hash` the caller supplies, as long as the signature over it is valid.

---

### Impact Explanation

A single TEE-attested MPC node can:

1. Observe a legitimately completed `respond_verify_foreign_tx(request_A, {payload_hash_A, sig_A})` on-chain.
2. Find any other pending `verify_foreign_transaction` request `request_B` in `pending_verify_foreign_tx_requests`.
3. Submit `respond_verify_foreign_tx(request_B, {payload_hash_A, sig_A})` before the honest nodes submit the correct response.
4. The contract passes the signature check (sig_A is valid over payload_hash_A under the root key) and resolves the yield for `request_B` with the wrong `{payload_hash_A, sig_A}`.

The caller of `request_B` receives a signed attestation that appears to certify a foreign-chain transaction, but the certified hash corresponds to a completely different transaction (`request_A`). Any downstream bridge contract or application that trusts the MPC attestation without independently recomputing the expected payload hash from the raw transaction data will accept this forged proof, enabling invalid bridge execution or double-spend conditions.

**Impact category**: High — forged foreign-chain verification causing invalid bridge execution.

---

### Likelihood Explanation

- The attacker is a single TEE-attested MPC node; no threshold collusion is required.
- The attacker only needs to reuse an already-existing valid `{payload_hash, signature}` pair — no new threshold signing is needed.
- The attack is a race against honest nodes, but a node with low-latency NEAR RPC access can reliably win the race, especially when honest nodes are busy with other requests.
- The `pending_verify_foreign_tx_requests` map is publicly observable on-chain, so the attacker can trivially enumerate targets.

---

### Recommendation

1. **Store block entropy in the on-chain request key.** At `verify_foreign_transaction` time, read `env::block_timestamp()` or a block-level entropy value and include it in `VerifyForeignTransactionRequest`. This allows the contract to recompute the expected payload hash.

2. **Verify `payload_hash` on-chain.** In `respond_verify_foreign_tx`, recompute the canonical `ForeignTxSignPayload` from `request.request`, `request.payload_version`, and the stored entropy, hash it, and assert `response.payload_hash == expected_hash` before accepting the response.

3. **Bind the request to the caller.** Include `predecessor_account_id` in `VerifyForeignTransactionRequest` (as `sign` does via `SignatureRequest::new`) so that a response for one caller cannot be injected into another caller's pending yield.

---

### Proof of Concept

```
// Step 1: Alice submits a legitimate request for Bitcoin tx A.
alice -> verify_foreign_transaction({ request: BtcTxA, domain_id: 0, payload_version: V1 })
// Honest nodes process it and submit:
honest_node -> respond_verify_foreign_tx(
    request = { BtcTxA, domain_id:0, version:V1 },
    response = { payload_hash: H(BtcTxA), signature: sig_A }
)
// Alice receives { payload_hash: H(BtcTxA), sig_A } — correct.

// Step 2: Bob submits a request for Bitcoin tx B.
bob -> verify_foreign_transaction({ request: BtcTxB, domain_id: 0, payload_version: V1 })
// pending_verify_foreign_tx_requests now contains { BtcTxB, 0, V1 } -> [bob_yield_id]

// Step 3: Malicious attested node replays sig_A against Bob's pending request.
malicious_node -> respond_verify_foreign_tx(
    request = { BtcTxB, domain_id:0, version:V1 },   // Bob's pending request
    response = { payload_hash: H(BtcTxA), signature: sig_A }  // Alice's old response
)
// Contract checks: verify_ecdsa_signature(sig_A, H(BtcTxA), root_pk) -> OK
// Contract resolves Bob's yield with { payload_hash: H(BtcTxA), sig_A }

// Step 4: Bob receives { payload_hash: H(BtcTxA), sig_A }.
// Bob's bridge contract verifies sig_A over H(BtcTxA) -> valid.
// Bridge believes BtcTxA was verified for Bob's request, not BtcTxB.
// Bob can use this to claim funds corresponding to BtcTxA even though BtcTxB was never verified.
```

### Citations

**File:** crates/contract/src/lib.rs (L526-531)
```rust
        self.check_request_preconditions(
            request.domain_id,
            DomainPurpose::ForeignTx,
            Gas::from_tgas(self.config.sign_call_gas_attachment_requirement_tera_gas),
            MINIMUM_SIGN_REQUEST_DEPOSIT,
        );
```

**File:** crates/contract/src/lib.rs (L596-608)
```rust
                    .as_affine();
                let expected_public_key =
                    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;

                let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");

                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
                .is_ok()
```

**File:** crates/contract/src/lib.rs (L718-753)
```rust
        let signature_is_valid = match (&response.signature, public_key) {
            (
                dtos::SignatureResponse::Secp256k1(signature_response),
                PublicKeyExtended::Secp256k1 { near_public_key },
            ) => {
                let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)
                    .expect("Secp256k1 variant always has a secp256k1 key");

                let payload_hash: [u8; 32] = response.payload_hash.0;

                // Check the signature is correct against the root public key
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    &payload_hash,
                    &secp_pk,
                )
                .is_ok()
            }
            (signature_response, public_key_requested) => {
                return Err(RespondError::SignatureSchemeMismatch {
                    mpc_scheme: Box::new(signature_response.clone()),
                    user_scheme: Box::new(public_key_requested),
                }
                .into());
            }
        };

        if !signature_is_valid {
            return Err(RespondError::InvalidSignature.into());
        }

        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L124-128)
```rust
pub struct VerifyForeignTransactionRequest {
    pub request: ForeignChainRpcRequest,
    pub domain_id: DomainId,
    pub payload_version: ForeignTxPayloadVersion,
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

**File:** crates/node/src/types.rs (L158-168)
```rust
pub struct VerifyForeignTxRequest {
    /// The unique ID that identifies the verify_foreign_tx, and can also uniquely identify the response.
    pub id: VerifyForeignTxId,
    /// The receipt that generated the verify_foreign_tx request, which can be used to look up on chain.
    pub receipt_id: CryptoHash,
    pub request: dtos::ForeignChainRpcRequest,
    pub payload_version: dtos::ForeignTxPayloadVersion,
    pub entropy: [u8; 32],
    pub timestamp_nanosec: u64,
    pub domain_id: DomainId,
}
```
