### Title
Unbound `payload_hash` in `respond_verify_foreign_tx` Enables Single-Participant Signature Replay Across Foreign-Tx Verification Requests - (File: crates/contract/src/lib.rs)

---

### Summary

`respond_verify_foreign_tx` accepts a caller-supplied `response.payload_hash` and verifies only that the submitted signature is valid over it. The contract never checks that `payload_hash` corresponds to the actual foreign-chain data in the pending `VerifyForeignTransactionRequest`. A single Byzantine attested participant can replay any previously observed valid signature (over any hash produced by the MPC foreign-tx key) to forge a foreign-chain verification response for a different pending request, consuming that request and delivering incorrect attestation data to the caller.

---

### Finding Description

In `respond_verify_foreign_tx` at `crates/contract/src/lib.rs:718-734`, the contract extracts `payload_hash` directly from the caller-supplied response and verifies the signature against it:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;
// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,
    &secp_pk,
)
.is_ok()
``` [1](#0-0) 

The `payload_hash` is never validated against the `VerifyForeignTransactionRequest` stored in the pending map. The correct hash should be `SHA256(borsh(ForeignTxSignPayload::V1 { request, extracted_values }))`, as defined in `ForeignTxSignPayload::compute_msg_hash()`. [2](#0-1) 

Compare this to the regular `respond` function, which derives the payload hash from the stored request — not from the response — making the signature binding to the correct payload:

```rust
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    payload_hash,
    &expected_public_key,
)
``` [3](#0-2) 

For foreign-tx verification, the node-side code uses a zero tweak (`Tweak::new([0u8; 32])`), meaning all foreign-tx signatures are produced under the same root key: [4](#0-3) 

This means any signature produced by the MPC network for any foreign-tx verification request is valid over the root key and can be replayed against any other pending foreign-tx request.

After the forged `respond_verify_foreign_tx` call succeeds, `resolve_yields_for` removes the pending request from the map and delivers the incorrect response to the caller: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

A bridge contract that calls `verify_foreign_transaction` and trusts the MPC contract's response receives a `VerifyForeignTransactionResponse` whose `payload_hash` does not correspond to the actual foreign transaction it requested verification for. The pending request is permanently consumed. If the bridge contract does not independently recompute and compare the expected payload hash (as the SDK's `ForeignChainSignatureVerifier::verify_signature` does), it will accept a forged attestation, enabling invalid bridge execution or double-spend conditions. [7](#0-6) 

---

### Likelihood Explanation

The attacker must be an attested participant (requires TEE attestation). Once attested, the attack requires only:

1. Observing any prior `respond_verify_foreign_tx` on-chain transaction (all signatures are public)
2. Waiting for a new `verify_foreign_transaction` request to appear in the pending map
3. Calling `respond_verify_foreign_tx` with the replayed `(payload_hash, signature)` pair and the new request

No threshold cooperation is needed. The attack is deterministic and requires no special timing or cryptographic capability beyond holding a valid TEE attestation.

---

### Recommendation

The contract must bind `response.payload_hash` to the pending request. Since the contract cannot re-run the foreign-chain inspection, the response should include the extracted values, and the contract should recompute and verify the hash:

```rust
// In respond_verify_foreign_tx:
let expected_payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    values: response.extracted_values.clone(),
});
let expected_hash = expected_payload.compute_msg_hash()
    .map_err(|_| RespondError::InvalidPayloadHash)?;
if expected_hash != response.payload_hash {
    return Err(RespondError::InvalidPayloadHash.into());
}
```

This mirrors how `respond` binds the signature to the payload stored in the pending request, preventing replay of signatures across different requests.

---

### Proof of Concept

1. Alice submits `verify_foreign_transaction` for Bitcoin tx A.
2. MPC nodes process it; the leader calls `respond_verify_foreign_tx` with `payload_hash = H_A` and `signature = sig_A`. This transaction is publicly visible on-chain.
3. Bob submits `verify_foreign_transaction` for Bitcoin tx B (a different transaction).
4. Byzantine attested participant observes `(H_A, sig_A)` from step 2 and calls `respond_verify_foreign_tx` with:
   - `request`: Bob's pending request (tx B)
   - `response.payload_hash`: `H_A`
   - `response.signature`: `sig_A`
5. The contract checks: Bob's request exists in `pending_verify_foreign_tx_requests` ✓; `sig_A` is a valid ECDSA signature over `H_A` under the root key ✓; no check that `H_A` corresponds to tx B ✗.
6. Bob's request is consumed; Bob's bridge contract receives `(H_A, sig_A)` as the verified response for tx B.
7. If Bob's bridge contract does not recompute the expected hash for tx B and compare it to `H_A`, it accepts the forged attestation and proceeds with invalid bridge execution.

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

**File:** crates/near-mpc-contract-interface/src/types/foreign_chain.rs (L1504-1509)
```rust
impl ForeignTxSignPayload {
    pub fn compute_msg_hash(&self) -> std::io::Result<Hash256> {
        let mut hasher = sha2::Sha256::new();
        borsh::BorshSerialize::serialize(self, &mut hasher)?;
        Ok(Hash256(hasher.finalize().into()))
    }
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L39-47)
```rust
    Ok(SignatureRequest {
        id: request.id,
        receipt_id: request.receipt_id,
        payload: Payload::Ecdsa(payload_bytes),
        tweak: Tweak::new([0u8; 32]),
        entropy: request.entropy,
        timestamp_nanosec: request.timestamp_nanosec,
        domain: request.domain_id,
    })
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

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L41-89)
```rust
impl ForeignChainSignatureVerifier {
    pub fn verify_signature(
        self,
        response: &VerifyForeignTransactionResponse,
        // TODO(#2232): don't use interface API types for public keys
        public_key: &PublicKey,
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
        let verification_result = match (public_key, &response.signature) {
            (
                PublicKey::Secp256k1(secp256k1_public_key),
                SignatureResponse::Secp256k1(k256_signature),
            ) => near_mpc_signature_verifier::verify_ecdsa_signature(
                k256_signature,
                &expected_payload_hash,
                secp256k1_public_key,
            ),
            (PublicKey::Ed25519(ed25519_public_key), SignatureResponse::Ed25519 { signature }) => {
                near_mpc_signature_verifier::verify_eddsa_signature(
                    signature,
                    expected_payload_hash.as_slice(),
                    ed25519_public_key,
                )
            }
            // TODO(#2234): improve types so these errors can't happen
            (PublicKey::Bls12381(_bls12381_g2_public_key), _) => {
                return Err(VerifyForeignChainError::UnexpectedSignatureScheme);
            }
            _ => return Err(VerifyForeignChainError::UnexpectedSignatureScheme),
        };

        verification_result.map_err(|_| VerifyForeignChainError::SignatureVerificationFailed)
    }
```
