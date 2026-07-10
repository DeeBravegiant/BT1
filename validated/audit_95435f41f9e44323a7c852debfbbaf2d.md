### Title
`respond_ckd` Skips Cryptographic Output Validation for `AppPublicKey` Variant, Allowing a Single Malicious Participant to Deliver a Forged CKD Response - (File: `crates/contract/src/lib.rs`)

---

### Summary

`MpcContract::respond_ckd` applies a cryptographic pairing check (`ckd_output_check`) only when the request uses the `AppPublicKeyPV` variant. For the `AppPublicKey` variant it performs **no validation of the response at all**, accepting any arbitrary `CKDResponse` bytes. A single Byzantine attested participant can therefore call `respond_ckd` with a forged response for any pending `AppPublicKey` CKD request and have it accepted and delivered to the user, bypassing the threshold requirement entirely.

---

### Finding Description

`respond_ckd` branches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no check
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` verifies the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, which cryptographically proves the response was computed from the correct network key and app identity. [2](#0-1) 

For `AppPublicKey`, the arm is an empty block. After the match, `resolve_yields_for` is called unconditionally, delivering whatever bytes the caller supplied. [3](#0-2) 

The only gate before this point is `assert_caller_is_attested_participant_and_protocol_active`, which requires the caller to be a single attested participant — not a threshold quorum. [4](#0-3) 

The inconsistency is structural: `AppPublicKeyPV` requests are cryptographically protected against a rogue responder; `AppPublicKey` requests are not. This mirrors the external report's pattern exactly — one code path checks the required condition, the other silently skips it.

---

### Impact Explanation

**Critical — Unauthorized confidential key derivation output without required participant authorization.**

A single malicious attested participant can:

1. Observe a pending `AppPublicKey` CKD request in contract state.
2. Craft an arbitrary `CKDResponse` (e.g., `big_y` and `big_c` set to attacker-controlled group elements encoding a key the attacker knows).
3. Call `respond_ckd` with the forged response before honest participants submit the real one.
4. The contract accepts the response, resolves the yield, and delivers the forged secret material to the user.

The user's application receives a derived secret key that is entirely controlled by the attacker. If the attacker sets `big_y` to an encryption of a key they know, they can decrypt all data the user subsequently encrypts under that derived key. This bypasses the threshold: normally `t` participants must cooperate off-chain to compute the CKD; here one participant suffices to substitute an arbitrary result.

---

### Likelihood Explanation

Any single attested participant who turns Byzantine can execute this attack. The attacker needs no special privilege beyond being in the current participant set with a valid TEE attestation. The attack is race-condition-free if the attacker acts before honest nodes submit the real response (which is the normal case since the attacker controls their own submission timing). `AppPublicKey` is the default/legacy variant and is used by callers who do not opt into public verifiability, making it the common case.

---

### Recommendation

Apply the same cryptographic output check to `AppPublicKey` responses, or reject `AppPublicKey` requests in `respond_ckd` until an equivalent verification is defined. At minimum, the two arms must be symmetric in their security guarantees. One option is to require that `big_y` in the response matches the expected derivation from the network public key and `app_id`, analogous to how `respond` verifies the ECDSA/EdDSA signature against the derived public key before resolving the yield. [5](#0-4) 

---

### Proof of Concept

1. Deploy the contract in Running state with a BLS12381 CKD domain.
2. User calls `request_app_private_key` with `AppPublicKey(some_g1_point)`.
3. Malicious attested participant calls `respond_ckd` with:
   ```json
   {
     "request": <the pending CKDRequest>,
     "response": {
       "big_y": "<attacker-chosen G1 point>",
       "big_c": "<attacker-chosen G1 point>"
     }
   }
   ```
4. `respond_ckd` passes all checks (attested participant, running state, BLS domain), hits the `AppPublicKey(_) => {}` arm, and calls `resolve_yields_for` with the forged bytes.
5. The user's promise callback fires and receives the attacker-controlled `CKDResponse` as the legitimate derivation output.
6. No error is returned; the pending request is removed from state as if successfully served. [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L586-644)
```rust
        let signature_is_valid = match (&response, public_key) {
            (
                dtos::SignatureResponse::Secp256k1(signature_response),
                PublicKeyExtended::Secp256k1 { near_public_key },
            ) => {
                // generate the expected public key
                let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)
                    .expect("Secp256k1 variant always has a secp256k1 key");
                let affine = *k256::PublicKey::try_from(&secp_pk)
                    .expect("stored key is always valid")
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
            }
            (
                dtos::SignatureResponse::Ed25519 { signature },
                PublicKeyExtended::Ed25519 {
                    edwards_point: public_key_edwards_point,
                    ..
                },
            ) => {
                let derived_public_key_edwards_point = derive_public_key_edwards_point_ed25519(
                    &public_key_edwards_point,
                    &request.tweak,
                );
                let derived_public_key_32_bytes =
                    dtos::Ed25519PublicKey::from(derived_public_key_edwards_point.compress());

                let message = request.payload.as_eddsa().expect("Payload is not EdDSA");

                near_mpc_signature_verifier::verify_eddsa_signature(
                    signature,
                    message,
                    &derived_public_key_32_bytes,
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

**File:** crates/contract/src/lib.rs (L653-689)
```rust
    #[handle_result]
    pub fn respond_ckd(&mut self, request: CKDRequest, response: CKDResponse) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();
        log!("respond_ckd: signer={}, request={:?}", &signer, &request);

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }

        self.assert_caller_is_attested_participant_and_protocol_active();

        let PublicKeyExtended::Bls12381 {
            public_key: dtos::PublicKey::Bls12381(public_key),
        } = self.public_key_extended(request.domain_id)?
        else {
            env::panic_str("Domain is not compatible with CKD (expected Bls12381 curve)");
        };

        match &request.app_public_key {
            dtos::CKDAppPublicKey::AppPublicKey(_) => {}
            dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
                if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
                    env::panic_str("CKD output check failed");
                }
            }
        }

        pending_requests::resolve_yields_for(
            &mut self.pending_ckd_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
    }
```

**File:** crates/contract/src/primitives/ckd.rs (L76-102)
```rust
/// Check that `e(big_c, g2) = e(big_y, app_pk2) . e(hash_point, public_key)`.
///
/// Point validation is fully delegated to the host, as in
/// [`app_public_key_check`].
pub(crate) fn ckd_output_check(
    app_id: &dtos::CkdAppId,
    output: &CKDResponse,
    app_public_key: &dtos::CKDAppPublicKeyPV,
    public_key: &dtos::Bls12381G2PublicKey,
) -> bool {
    let big_c = env::bls12381_p1_decompress(&output.big_c);
    let big_y = env::bls12381_p1_decompress(&output.big_y);
    let pk2 = env::bls12381_p2_decompress(&app_public_key.pk2);
    let pk = env::bls12381_p2_decompress(public_key);
    let hash_point = hash_app_id_with_pk(public_key.as_slice(), app_id.as_ref());

    let pairing_input = [
        big_c.as_slice(),
        MINUS_G2_GENERATOR_UNCOMPRESSED.as_slice(),
        big_y.as_slice(),
        pk2.as_slice(),
        hash_point.as_slice(),
        pk.as_slice(),
    ]
    .concat();
    env::bls12381_pairing_check(&pairing_input)
}
```
