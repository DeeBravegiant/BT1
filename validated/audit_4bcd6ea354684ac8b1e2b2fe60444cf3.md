### Title
Single Byzantine Participant Can Deliver Fabricated CKD Response for `AppPublicKey` Requests, Bypassing Threshold Requirement — (`crates/contract/src/lib.rs`)

---

### Summary

The `respond_ckd()` function in `MpcContract` performs no cryptographic verification of the response when the CKD request uses the `CKDAppPublicKey::AppPublicKey` (legacy/private) variant. A single attested participant — strictly below the signing threshold — can call `respond_ckd()` with an entirely fabricated `(big_y, big_c)` pair, and the contract will accept and deliver it to the user. This bypasses the threshold requirement that is the core security guarantee of the MPC network.

---

### Finding Description

In `respond_ckd()`, the contract branches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← NO CHECK
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, the contract calls `ckd_output_check`, which cryptographically verifies that the response is consistent with the MPC master public key and the request's `app_id`. For `AppPublicKey`, the branch is a no-op — any `(big_y, big_c)` pair is accepted unconditionally.

The caller is required to be an attested participant:

```rust
self.assert_caller_is_attested_participant_and_protocol_active();
``` [2](#0-1) 

But this check only requires **one** attested participant — not a threshold. Once the fabricated response passes, `resolve_yields_for` delivers it to every queued caller:

```rust
pending_requests::resolve_yields_for(
    &mut self.pending_ckd_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [3](#0-2) 

Compare with `respond()` for ECDSA/EdDSA signatures, which always verifies the response against the derived public key before resolving: [4](#0-3) 

The asymmetry is exact: `respond()` and `respond_ckd()` with `AppPublicKeyPV` both have cryptographic output verification; `respond_ckd()` with `AppPublicKey` has none.

---

### Impact Explanation

**Impact: Critical — Unauthorized confidential key derivation output without required participant authorization.**

A Byzantine participant can set `big_y = identity_G1` (the additive identity, a valid BLS12-381 G1 point) and `big_c = k·G1` for any scalar `k` they choose. The user decrypts:

```
confidential_key = big_c − app_private_key · big_y
                 = k·G1 − app_private_key · 0
                 = k·G1
```

The attacker knows `k`, so they know the user's "confidential key" in full. The user has no way to detect this on-chain (the `AppPublicKey` variant is by design privately verifiable only). Any application that relies on this derived key — for signing, authentication, or secret storage — is now under the attacker's control.

This directly satisfies: *"Unauthorized… confidential key derivation output without the required participant authorization"* and *"Bypass of threshold-signature requirements… that materially enables forgery or secret recovery."*

---

### Likelihood Explanation

Any single attested participant who turns Byzantine can execute this attack. The MPC system's threat model explicitly tolerates Byzantine participants below the threshold — that is the entire purpose of threshold cryptography. A single compromised TEE node, a malicious operator, or a node whose key material is leaked is sufficient. The attack requires no coordination, no network-level access, and no special timing beyond observing a pending `AppPublicKey` CKD request on-chain (which is public).

---

### Recommendation

1. **Require `AppPublicKeyPV` for all new CKD requests.** Deprecate `AppPublicKey` at the contract level so that on-chain verification is always possible.

2. **If `AppPublicKey` must remain supported**, add a threshold-agreement mechanism: require that at least `t` distinct attested participants submit the same `(big_y, big_c)` before the response is resolved, analogous to how the off-chain MPC protocol requires threshold participation before a signature is produced.

3. **Validate that `big_y` is not the identity element** as a minimal sanity check, since the identity-point attack vector is the simplest fabrication.

---

### Proof of Concept

1. User calls `request_app_private_key` with `CKDAppPublicKey::AppPublicKey(user_pk)`. A `CKDRequest` is queued in `pending_ckd_requests`.

2. Byzantine participant (one attested node) observes the pending request on-chain.

3. Byzantine participant calls `respond_ckd(ckd_request, CKDResponse { big_y: G1_IDENTITY, big_c: k·G1 })` for attacker-chosen scalar `k`.

4. `respond_ckd` passes `assert_caller_is_attested_participant_and_protocol_active()` (single participant suffices), enters the `AppPublicKey` branch with no check, and calls `resolve_yields_for`, delivering the fabricated response.

5. User receives `(big_y=0, big_c=k·G1)` and decrypts `confidential_key = k·G1 − app_private_key·0 = k·G1`.

6. Attacker knows `k` and therefore knows the user's confidential key, enabling full impersonation in any downstream application. [5](#0-4)

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
