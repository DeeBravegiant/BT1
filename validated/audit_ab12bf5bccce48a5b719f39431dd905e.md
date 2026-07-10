### Title
Unbound `payload_hash` in `respond_verify_foreign_tx` Enables Cross-Request Signature Replay by a Single Byzantine Participant — (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_verify_foreign_tx` verifies only that the submitted ECDSA signature is valid over the caller-supplied `response.payload_hash`. It never checks that `payload_hash` was actually derived from the `request` (i.e., `SHA-256(borsh(ForeignTxSignPayload{request, extracted_values}))`). A single Byzantine MPC participant below the signing threshold can therefore take a legitimately-generated signature for foreign transaction A and use it to satisfy a completely different pending request for foreign transaction B, delivering a forged foreign-chain attestation to the waiting caller.

---

### Finding Description

In `respond_verify_foreign_tx` the on-chain verification step is:

```rust
let payload_hash: [u8; 32] = response.payload_hash.0;   // ← caller-supplied

// Check the signature is correct against the root public key
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    &payload_hash,          // ← never checked against `request`
    &secp_pk,
)
.is_ok()
``` [1](#0-0) 

The `request` parameter is used only as a map key to drain pending yields:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_verify_foreign_tx_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [2](#0-1) 

There is no assertion that `response.payload_hash == ForeignTxSignPayload::V1 { request, values }.compute_msg_hash()`.

Contrast this with the regular `respond` path, where the payload to verify against is taken directly from the request, not from the response:

```rust
let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");
near_mpc_signature_verifier::verify_ecdsa_signature(
    signature_response,
    payload_hash,
    &expected_public_key,
)
``` [3](#0-2) 

The SDK-side helper `ForeignChainSignatureVerifier::verify_signature` does enforce the binding, but that check runs in the caller's contract, not in the MPC contract:

```rust
let payload_is_correct = expected_payload_hash == response.payload_hash;
if !payload_is_correct {
    return Err(VerifyForeignChainError::IncorrectPayloadSigned { ... });
}
``` [4](#0-3) 

The MPC contract itself never performs this check.

---

### Impact Explanation

A single Byzantine MPC participant (strictly below the signing threshold) can:

1. Participate honestly in a threshold-signing round for foreign transaction A, obtaining a valid root-key signature `sig_A` over `hash(tx_A, values_A)`.
2. Observe that a different user has a pending `verify_foreign_transaction` request for transaction B.
3. Call `respond_verify_foreign_tx(request = tx_B, response = {payload_hash = hash(tx_A, values_A), signature = sig_A})`.
4. The contract accepts the call — `sig_A` is a valid signature over the provided `payload_hash`.
5. All yields queued under `tx_B` are resumed with the forged response.

The waiting bridge contract receives `{payload_hash = hash(tx_A, values_A), signature = sig_A}` as the attestation for `tx_B`. Any bridge contract that does not independently re-derive and compare the expected `payload_hash` (i.e., does not use `ForeignChainSignatureVerifier`) will treat this as a valid attestation that `tx_B` occurred, enabling invalid bridge execution (e.g., minting tokens on NEAR for a deposit that never happened on the foreign chain, or double-spending an inbound bridge transfer).

Even for callers that do verify correctly, the pending request for `tx_B` is permanently consumed; the user must resubmit and pay again, and the Byzantine node can repeat the attack indefinitely.

---

### Likelihood Explanation

- Requires exactly **one** compromised attested MPC participant — explicitly within the Byzantine-below-threshold threat model.
- No threshold collusion, no TEE break, no network-level attack is needed.
- The attacker only needs to have participated in any prior legitimate signing round to obtain a reusable `(payload_hash, signature)` pair.
- The attack is cheap and repeatable: the same `(payload_hash, sig)` pair can be replayed against any pending request whose `request` key the attacker can predict (all pending requests are observable on-chain).

---

### Recommendation

In `respond_verify_foreign_tx`, recompute the expected `payload_hash` from the `request` and the extracted values included in the response, then assert equality before accepting the signature:

```rust
// Reconstruct the canonical payload from the request and the
// extracted values the node claims to have observed.
let expected_payload = ForeignTxSignPayload::V1(ForeignTxSignPayloadV1 {
    request: request.request.clone(),
    values: response.extracted_values.clone(), // add to response DTO
});
let expected_hash = expected_payload.compute_msg_hash()
    .map_err(|_| RespondError::PayloadHashComputationFailed)?;
if expected_hash != response.payload_hash {
    return Err(RespondError::PayloadHashMismatch.into());
}
```

This mirrors the binding already enforced by `ForeignChainSignatureVerifier` in the SDK and by the `respond` path for regular signatures.

---

### Proof of Concept

```
1. Alice submits verify_foreign_transaction(tx_id=0xAAA, chain=Bitcoin, extractors=[BlockHash])
   → pending_verify_foreign_tx_requests[req_A] = [yield_alice]

2. Bob submits verify_foreign_transaction(tx_id=0xBBB, chain=Bitcoin, extractors=[BlockHash])
   → pending_verify_foreign_tx_requests[req_B] = [yield_bob]

3. MPC network honestly processes req_A:
   payload_A = ForeignTxSignPayload::V1 { request: req_A, values: [block_hash_A] }
   hash_A    = SHA-256(borsh(payload_A))
   sig_A     = MPC_threshold_sign(hash_A)   // requires t-of-n nodes

4. Byzantine node (1 of n, below threshold) calls:
   respond_verify_foreign_tx(
       request  = req_B,                          // Bob's pending request
       response = { payload_hash: hash_A,         // hash of Alice's tx
                    signature:    sig_A }          // signature over Alice's tx
   )

5. Contract check (lib.rs:726-734):
   verify_ecdsa_signature(sig_A, hash_A, root_pk) → OK  ✓
   (no check that hash_A == hash(req_B, values_B))

6. resolve_yields_for(pending_verify_foreign_tx_requests, req_B, response)
   → yield_bob resumed with { payload_hash: hash_A, signature: sig_A }

7. Bob's bridge contract receives attestation for tx_BBB but the
   signed payload actually attests to tx_AAA.
   If Bob's contract skips ForeignChainSignatureVerifier's hash check,
   it accepts the forged attestation and executes bridge logic for tx_BBB.
``` [5](#0-4) [6](#0-5)

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

**File:** crates/near-mpc-sdk/src/foreign_chain.rs (L42-89)
```rust
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
