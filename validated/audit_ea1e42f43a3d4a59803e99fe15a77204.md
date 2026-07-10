### Title
Missing CKD Output Verification for Legacy `AppPublicKey` Variant Allows Byzantine Participant to Deliver Forged Key Material — (`File: crates/contract/src/lib.rs`)

---

### Summary

`respond_ckd` applies a cryptographic pairing check (`ckd_output_check`) only for the `AppPublicKeyPV` variant of a CKD request. For the legacy `AppPublicKey` variant, the response arm is an empty no-op. A single Byzantine MPC participant (strictly below the signing threshold) can call `respond_ckd` at any time with an arbitrary, attacker-crafted `CKDResponse`, and the contract will accept and deliver it to the waiting user without any verification.

---

### Finding Description

In `respond_ckd`, the response verification is gated on the request variant:

```rust
// crates/contract/src/lib.rs, lines 675-682
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← NO VERIFICATION
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` enforces the pairing equation `e(big_c, G2) = e(big_y, app_pk2) · e(H(pk, app_id), pk)`, which cryptographically binds the response to the MPC network's master secret key and the user's ephemeral public key. [2](#0-1) 

For `AppPublicKey` (the legacy, default format used in the CLI and e2e tests), the match arm is `{}` — the contract performs **zero cryptographic verification** of the response before resolving the user's yield. [3](#0-2) 

The `respond_ckd` function is callable by any single attested participant — there is no threshold-of-agreement requirement on the response path. [4](#0-3) 

Compare this to `respond` (for ECDSA/EdDSA signatures), which always verifies the signature cryptographically before resolving the yield, regardless of request variant. [5](#0-4) 

The `AppPublicKey` format is the primary/legacy format. It is the default in the CLI example and in the e2e test `ckd_response__passes_cryptographic_verification`. [6](#0-5) 

---

### Impact Explanation

A Byzantine MPC participant can call `respond_ckd` with a fully attacker-controlled `CKDResponse{big_y, big_c}` for any pending `AppPublicKey` CKD request. Because the contract performs no verification, it resolves the user's yield with the forged values.

The user (or TEE app) then computes:

```
sig = big_c - a · big_y
```

where `a` is their ephemeral private key. If the attacker sets `big_y = identity (0·G1)` and `big_c = t·G1` for any scalar `t` of their choosing, the user computes `sig = t·G1`, and derives key `s = HKDF(t·G1)` — a key entirely controlled by the attacker. The user has no way to detect the forgery because the `AppPublicKey` variant provides no public verifiability.

This means a single Byzantine participant (below the signing threshold) can:
- Cause a TEE application to derive and use an attacker-known secret key instead of the legitimate MPC-derived key.
- Impersonate the TEE app on any chain or system where that derived key controls assets or identity.

This is a **Critical** impact: unauthorized confidential key derivation output without the required threshold participant authorization, enabling secret recovery by the attacker.

---

### Likelihood Explanation

- The `AppPublicKey` (legacy) format is the default and most commonly used variant.
- Any single attested MPC participant can call `respond_ckd` at any time — no threshold agreement is required.
- The attack requires only that the Byzantine node races to call `respond_ckd` before the honest leader does, or that the Byzantine node is selected as the protocol leader for that request.
- No special privileges, key material, or external dependencies are needed beyond being an active attested participant.

---

### Recommendation

Add the same cryptographic output verification for the `AppPublicKey` variant in `respond_ckd`. For the legacy variant, the contract already holds the MPC network's BLS public key (`public_key` retrieved at line 668–673). A verification analogous to `ckd_output_check` can be constructed using only the G1 public key `A` from the request and the network public key, verifying that `e(big_c, G2) = e(H(pk, app_id), pk) · e(big_y, A_as_G2)` — or alternatively, mandate migration to `AppPublicKeyPV` for all new requests and reject `AppPublicKey` responses without verification. [7](#0-6) 

---

### Proof of Concept

1. User submits `request_app_private_key` with `AppPublicKey(A)` (legacy format), attaching 1 yoctoNEAR deposit. Request is queued in `pending_ckd_requests`.

2. Byzantine attested participant calls `respond_ckd(request, CKDResponse { big_y: identity_point, big_c: t·G1 })` where `t` is any scalar the attacker chooses.

3. `respond_ckd` passes all checks:
   - `assert_caller_is_attested_participant_and_protocol_active()` — passes (Byzantine node is a valid participant).
   - `is_running_or_resharing()` — passes.
   - `accept_requests` — passes.
   - Match arm for `AppPublicKey` — empty `{}`, no verification.

4. `pending_requests::resolve_yields_for` resolves the user's yield with the forged response.

5. User receives `(big_y=0, big_c=t·G1)` and computes `sig = t·G1 - a·0 = t·G1`, then `s = HKDF(t·G1)`. The attacker, who chose `t`, knows `s` exactly. [8](#0-7) [9](#0-8)

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

**File:** crates/contract/src/lib.rs (L653-666)
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
```

**File:** crates/contract/src/lib.rs (L668-688)
```rust
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

**File:** crates/e2e-tests/tests/ckd_verification.rs (L40-91)
```rust
/// Verify that a CKD response (AppPublicKey variant) is mathematically correct.
#[tokio::test]
#[expect(non_snake_case)]
async fn ckd_response__passes_cryptographic_verification() {
    // given
    let (cluster, running) =
        common::must_setup_cluster(common::CKD_VERIFICATION_PORT_SEED, |_| {}).await;

    let bls_domain = running
        .domains
        .domains
        .iter()
        .find(|d| {
            Curve::from(d.protocol) == Curve::Bls12381 && matches!(d.purpose, DomainPurpose::CKD)
        })
        .expect("no Bls12381 CKD domain found")
        .clone();

    let mpc_pk = common::must_get_bls_public_key(&running, bls_domain.id);
    let user = cluster.default_user_account().clone();

    let mut rng = rand::rngs::StdRng::seed_from_u64(1);
    let private_key = Scalar::random(&mut rng);
    let app_public_key = CKDAppPublicKey::AppPublicKey(Bls12381G1PublicKey::from(
        &(G1Projective::generator() * private_key),
    ));

    // when
    let outcome = cluster
        .send_ckd_request(bls_domain.id, app_public_key, &user)
        .await
        .expect("CKD request transaction failed");

    // then
    assert!(
        outcome.is_success(),
        "CKD request failed: {:?}",
        outcome.failure_message()
    );

    let response: serde_json::Value = outcome.json().expect("failed to deserialize CKD response");
    let big_y: Bls12381G1PublicKey =
        serde_json::from_value(response["big_y"].clone()).expect("failed to parse big_y");
    let big_c: Bls12381G1PublicKey =
        serde_json::from_value(response["big_c"].clone()).expect("failed to parse big_c");

    assert!(
        verify_ckd(&user, DERIVATION_PATH, &mpc_pk, private_key, &big_y, &big_c)
            .expect("verify_ckd failed"),
        "CKD response failed cryptographic verification"
    );
}
```
