### Title
`respond_verify_foreign_tx` Accepts Replayed `(payload_hash, signature)` From Any Prior Response Against Any Pending Request - (File: `crates/contract/src/lib.rs`)

### Summary

`respond_verify_foreign_tx` verifies that the submitted signature is valid over the caller-supplied `response.payload_hash`, but never checks that `response.payload_hash` actually corresponds to the pending `request`. A single Byzantine MPC participant (below signing threshold) can replay any previously-observed legitimate `(payload_hash, signature)` pair against any currently-pending foreign-transaction verification request, resolving it with wrong data and permanently preventing the correct response from being delivered.

### Finding Description

In `respond_verify_foreign_tx` (`crates/contract/src/lib.rs`, lines 691–754), the signature validity check is:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;
// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
```

`payload_hash` is taken directly from `response.payload_hash` — a caller-supplied value. The contract only checks that the signature is valid over that hash. It never checks that `payload_hash == SHA-256(borsh(ForeignTxSignPayload { request: request.request, values: ... }))`, i.e., that the hash actually commits to the pending request's foreign-chain transaction.

Compare this to the regular `respond` function (lines 600–608), where the payload hash is derived from the request itself:

```rust
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    payload_hash,
    &expected_public_key,
)
```

In `respond`, the hash is bound to the request — no replay is possible. In `respond_verify_foreign_tx`, the hash is free-floating and only checked for signature validity.

After the signature check passes, `resolve_yields_for` drains the entire pending queue for `request` and resumes all queued yields with `serde_json::to_vec(&response)` — the wrong response bytes:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
```

Once drained, the map entry is gone. The legitimate MPC response, when it eventually arrives, will find `RequestNotFound` and fail.

### Impact Explanation

A single malicious MPC participant (below signing threshold) who has observed any prior legitimate `respond_verify_foreign_tx` call on-chain (all NEAR transactions are public) can:

1. **Corrupt any pending foreign-tx verification**: Drain the pending queue for any `verify_foreign_transaction` request with a stale `(payload_hash, signature)` from a different transaction. The bridge caller's yield is resolved with the wrong `VerifyForeignTransactionResponse`.
2. **Forge foreign-chain verification**: If the bridge contract does not independently re-verify `payload_hash` against its expected values (using `ForeignChainSignatureVerifier::verify_signature` from the SDK), it will accept a response attesting to a different foreign-chain transaction than the one it requested — enabling invalid bridge execution or double-spend conditions.
3. **Permanently block legitimate responses**: Once `resolve_yields_for` drains the queue, the legitimate MPC response returns `RequestNotFound` and is discarded. The bridge caller cannot retry without submitting a new request.

This maps to the allowed High impact: *"Cross-chain replay, forged foreign-chain verification... that causes invalid bridge execution or double-spend conditions."*

### Likelihood Explanation

- **Attacker profile**: A single registered MPC participant with valid TEE attestation — explicitly in scope as "Byzantine participant strictly below the signing threshold."
- **No threshold collusion required**: One participant acting alone is sufficient.
- **Replay material is freely available**: All prior `respond_verify_foreign_tx` calls are on-chain and publicly observable. Any `(payload_hash, signature)` from any past call is permanently reusable.
- **Trigger condition**: Any live pending `verify_foreign_transaction` request in the contract — a routine condition in any active bridge deployment.

### Recommendation

In `respond_verify_foreign_tx`, recompute the expected `payload_hash` from the pending `request` and the `response.values` (or require the response to include the full `ForeignTxSignPayload` so the contract can hash it), then assert `response.payload_hash == expected_hash` before accepting the signature. This binds the signature check to the specific request being resolved, eliminating the replay surface. The regular `respond` function already demonstrates the correct pattern: the payload hash is derived from the request, not accepted from the caller.

### Proof of Concept

```
// Setup: attacker is a registered MPC participant.
// Step 1: Observe a past legitimate call on-chain:
//   respond_verify_foreign_tx(request_A, response_A)
//   where response_A = { payload_hash: H_A, signature: sig_A }
//   and sig_A = ECDSA_sign(sk_domain, H_A)  [valid]

// Step 2: A new request is submitted by a bridge contract:
//   verify_foreign_transaction(request_B)  // for a different tx_id
//   → pending_verify_foreign_tx_requests[request_B] = [yield_B]

// Step 3: Attacker calls (as a single participant, no threshold needed):
//   respond_verify_foreign_tx(request_B, response_A)
//
// Contract checks:
//   verify_ecdsa_signature(sig_A, H_A, pk_domain) → OK  (sig_A is still valid)
//   pending_verify_foreign_tx_requests.get(request_B) → Some([yield_B])
//   → resolve_yields_for drains queue, resumes yield_B with response_A bytes
//
// Result:
//   - Bridge caller for request_B receives response_A (wrong tx hash + signature)
//   - pending_verify_foreign_tx_requests[request_B] is now empty
//   - Legitimate MPC response for request_B → RequestNotFound → discarded
//   - Bridge contract may accept forged verification if it skips SDK re-verification
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L600-608)
```rust
                let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");

                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
                .is_ok()
```

**File:** crates/contract/src/lib.rs (L718-734)
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
```

**File:** crates/contract/src/lib.rs (L749-753)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_verify_foreign_tx_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
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

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L47-64)
```rust
    ) -> Result<(), VerifyForeignChainError> {
        let expected_payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
            request: self.request,
            values: self.expected_extracted_values,
        });

        let expected_payload_hash = expected_payload
            .compute_msg_hash()
            .map_err(|_| VerifyForeignChainError::FailedToComputeMsgHash)?;

        let payload_is_correct = expected_payload_hash == response.payload_hash;

        if !payload_is_correct {
            return Err(VerifyForeignChainError::IncorrectPayloadSigned {
                got: response.payload_hash.clone(),
                expected: expected_payload_hash,
            });
        }
```
