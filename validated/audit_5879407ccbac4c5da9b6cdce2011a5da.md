### Title
`respond_verify_foreign_tx` Accepts Recycled Signatures Without Binding `payload_hash` to the Submitted Request - (File: crates/contract/src/lib.rs)

### Summary

The `respond_verify_foreign_tx` function in the MPC contract verifies that `response.signature` is valid over `response.payload_hash`, but never verifies that `response.payload_hash` was actually computed from the specific `request` parameter supplied in the same call. A single malicious attested participant (below the signing threshold) can recycle a valid `{payload_hash, signature}` pair from any previous on-chain response and use it to resolve an unrelated pending request, consuming the yield without providing a valid attestation for the requested transaction.

### Finding Description

In `crates/contract/src/lib.rs` lines 718–747, `respond_verify_foreign_tx` performs two independent checks and then resolves the yield:

1. **Signature validity**: verifies `signature` is valid over `response.payload_hash` against the domain's root public key.
2. **Request existence**: looks up `request` in `pending_verify_foreign_tx_requests` and drains the queue.

What is **absent** is any check that `response.payload_hash` was derived from the submitted `request`. The signed payload type is `ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 { request, values })`, whose hash is `SHA-256(borsh(ForeignTxSignPayload))`. This hash encodes the full `ForeignChainRpcRequest` (including `tx_id`, `extractors`, finality, etc.), making it specific to a particular transaction. However, the contract never reconstructs or compares this hash against the `request` argument.

Because every `respond_verify_foreign_tx` call is a public NEAR transaction, all previously submitted `{payload_hash: H_Y, signature: sig(H_Y)}` pairs are permanently visible on-chain. A malicious attested participant can:

1. Observe any prior valid response `{payload_hash: H_Y, signature: sig(H_Y)}` from the NEAR blockchain.
2. Wait for a new pending request X to appear in `pending_verify_foreign_tx_requests`.
3. Call `respond_verify_foreign_tx(request = X, response = {payload_hash: H_Y, signature: sig(H_Y)})`.
4. The contract accepts the call: the signature is valid over `H_Y`, and request X exists in the map.
5. `resolve_yields_for` drains the entire fan-out queue for X, resuming every queued yield with the incorrect response bytes.

The attacker requires no threshold collusion and no key material beyond what is already public on-chain.

### Impact Explanation

Every pending `verify_foreign_transaction` request for transaction X is irrevocably consumed with a response whose `payload_hash` corresponds to a different transaction Y. The NEAR yield-resume mechanism is one-shot: once resolved, the yield cannot be re-resolved. The caller's callback fires with `{payload_hash: H_Y, signature: sig(H_Y)}`.

If the bridge contract uses the SDK's `ForeignChainSignatureVerifier::verify_signature` (which checks `expected_payload_hash == response.payload_hash`), it detects the mismatch and the bridge operation fails — but the deposit is lost and the user must resubmit. If the bridge contract only checks signature validity without verifying the payload hash binding, it would accept the response as a valid attestation for transaction X, enabling forged foreign-chain verification and potentially invalid bridge execution (e.g., releasing funds for a transaction that was never verified).

This breaks the core production safety invariant of the `verify_foreign_transaction` flow: each submitted request must be resolved with a response that attests to the actual verification of the requested transaction.

### Likelihood Explanation

The attack requires only a single malicious attested MPC participant. The attacker:
- Passes `assert_caller_is_attested_participant_and_protocol_active` by virtue of being a registered participant.
- Obtains a valid `{payload_hash, signature}` pair for free from any prior on-chain `respond_verify_foreign_tx` transaction (no cryptographic capability needed).
- Targets any pending request visible in the contract state.

No threshold collusion, no key material, and no privileged access beyond being an attested participant is required.

### Recommendation

The contract should verify that `response.payload_hash` encodes the submitted `request`. Since the contract does not know the extracted values at response time, one approach is to restructure `ForeignTxSignPayload` to include a separately verifiable request commitment (e.g., `request_hash = SHA-256(borsh(request))`), store this commitment when the request is enqueued, and verify in `respond_verify_foreign_tx` that the `payload_hash` is consistent with the stored commitment. Alternatively, the response could carry the full `ForeignTxSignPayload` (not just its hash), allowing the contract to verify the binding directly — though this may exceed NEAR's promise-data size limits.

### Proof of Concept

```
1. Alice calls verify_foreign_transaction(request = X {tx_id: [0xAA; 32], ...})
   → pending_verify_foreign_tx_requests[X] = [yield_alice]

2. MPC network legitimately processes request Y {tx_id: [0xBB; 32], ...}
   → respond_verify_foreign_tx(request=Y, response={payload_hash: H_Y, sig: sig(H_Y)})
   → This NEAR transaction is publicly visible on-chain.

3. Malicious attested participant reads H_Y and sig(H_Y) from the NEAR blockchain.

4. Malicious participant calls:
   respond_verify_foreign_tx(
     request = X,                                    // Alice's pending request
     response = {payload_hash: H_Y, sig: sig(H_Y)}  // recycled from step 2
   )

5. Contract checks:
   - sig(H_Y) valid over H_Y against domain public key? YES ✓
   - request X in pending map? YES ✓
   → resolve_yields_for drains yield_alice with {payload_hash: H_Y, sig: sig(H_Y)}

6. Alice's callback fires. SDK verify_signature computes:
   expected = SHA-256(borsh(ForeignTxSignPayload{request: X, values: expected}))
   expected ≠ H_Y → VerifyForeignChainError::IncorrectPayloadSigned
   → Alice's bridge operation fails; yield is permanently consumed; Alice must resubmit.
```

**Relevant code locations:**

- Missing binding check: [1](#0-0) 
- `ForeignTxSignPayload` hash computation (encodes full request): [2](#0-1) 
- SDK-side binding check (not enforced by contract): [3](#0-2) 
- `resolve_yields_for` drains the full queue irreversibly: [4](#0-3)

### Citations

**File:** crates/contract/src/lib.rs (L718-747)
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
```

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1504-1509)
```rust
impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
        let mut hasher = sha2::Sha256::new();
        borsh::BorshSerialize::serialize(self, &mut hasher)?;
        Ok(Hash256(hasher.finalize().into()))
    }
```

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L57-64)
```rust
        let payload_is_correct = expected_payload_hash == response.payload_hash;

        if !payload_is_correct {
            return Err(VerifyForeignChainError::IncorrectPayloadSigned {
                got: response.payload_hash.clone(),
                expected: expected_payload_hash,
            });
        }
```

**File:** crates/contract/src/pending_requests.rs (L66-88)
```rust
pub(crate) fn resolve_yields_for<K>(
    requests: &mut LookupMap<K, Vec<YieldIndex>>,
    request: &K,
    response_bytes: Vec<u8>,
) -> Result<(), Error>
where
    K: BorshSerialize + BorshDeserialize + Clone + Ord,
{
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
