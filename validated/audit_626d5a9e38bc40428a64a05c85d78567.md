### Title
Unvalidated CKD Response for `AppPublicKey` Variant Allows Byzantine Participant to Deliver Forged Key Material - (File: crates/contract/src/lib.rs)

### Summary

`respond_ckd` validates the cryptographic correctness of the MPC response only for the `AppPublicKeyPV` variant. For the `AppPublicKey` variant, the contract performs no validation of the returned `CKDResponse` values (`big_y`, `big_c`) before resolving the yield and delivering the response to the requesting user. A single Byzantine attested participant (below the signing threshold) can call `respond_ckd` with an arbitrary forged `CKDResponse` for any queued `AppPublicKey` CKD request, and the contract will accept and deliver it.

### Finding Description

In `respond_ckd`, after verifying the caller is an attested participant and the protocol is active, the function branches on the `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← no validation
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` enforces the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, cryptographically binding the response to the master public key and the request's `app_id`. [2](#0-1) 

For `AppPublicKey`, the empty arm `{}` means any `big_y` and `big_c` values pass through unconditionally. The contract then calls `resolve_yields_for`, which drains the entire fan-out queue and delivers the forged response to every waiting caller. [3](#0-2) 

By contrast, `respond` (for threshold signatures) always verifies the signature cryptographically before resolving: [4](#0-3) 

The asymmetry is the root cause: one condition (caller authorization) is checked, but the second required condition (response cryptographic validity) is absent for the `AppPublicKey` path.

### Impact Explanation

A single Byzantine attested participant (strictly below the signing threshold) can:

1. Observe a queued `AppPublicKey` CKD request in `pending_ckd_requests`.
2. Construct an arbitrary `CKDResponse { big_y: <attacker-chosen G1 point>, big_c: <arbitrary> }`.
3. Call `respond_ckd` with the forged response — the contract accepts it and drains the entire yield queue, delivering the forged key material to all waiting callers.

The user receives a `big_y` that the attacker chose and for which the attacker holds the corresponding scalar (private key). Any data the user subsequently encrypts under this forged `big_y` is decryptable by the attacker. This breaks the core safety invariant of the CKD protocol: that the derived key material is produced honestly by the threshold MPC network and is not known to any individual participant.

**Impact category:** Medium — request-lifecycle and contract execution-flow manipulation that breaks a production safety/accounting invariant (integrity of delivered CKD key material) without requiring network-level DoS or operator misconfiguration.

### Likelihood Explanation

- The attacker must be an attested MPC participant (a Byzantine node below the signing threshold). This is explicitly within the allowed attacker profile.
- No threshold collusion is required — a single node suffices.
- The attack window is any block in which an `AppPublicKey` CKD request is pending. The `MAX_PENDING_REQUEST_FAN_OUT` cap of 128 means up to 128 callers can be affected by a single forged `respond_ckd` call. [5](#0-4) 

### Recommendation

Add the same cryptographic output check for `AppPublicKey` that exists for `AppPublicKeyPV`. For the `AppPublicKey` variant, the contract already holds the master BLS12-381 G2 public key and the `app_id`; a pairing-based check analogous to `ckd_output_check` should be applied. If a lightweight on-chain check is not feasible for the legacy variant, the contract should at minimum reject `respond_ckd` calls for `AppPublicKey` requests unless a valid proof of correct derivation is supplied, consistent with how `respond` rejects invalid signatures.

### Proof of Concept

1. User calls `request_app_private_key` with `AppPublicKey(some_g1_point)` and attaches 1 yoctoNEAR deposit. The request is queued in `pending_ckd_requests`.
2. Byzantine attested participant calls:
   ```
   respond_ckd(
     request = <the queued CKDRequest>,
     response = CKDResponse {
       big_y: <attacker-controlled G1 point>,
       big_c: <arbitrary G1 point>,
     }
   )
   ```
3. `respond_ckd` passes all checks (caller is attested participant, protocol is running, `accept_requests` is true), hits the `AppPublicKey(_) => {}` branch with no validation, and calls `resolve_yields_for` — delivering the forged response to the user.
4. The user's yield resumes with the attacker-chosen `big_y`. The attacker holds the scalar corresponding to `big_y` and can decrypt any data the user encrypts under it. [6](#0-5)

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

**File:** crates/contract/src/primitives/ckd.rs (L80-102)
```rust
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

**File:** crates/contract/src/pending_requests.rs (L37-37)
```rust
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;
```
