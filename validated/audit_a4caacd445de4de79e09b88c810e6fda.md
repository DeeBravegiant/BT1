### Title
`respond_ckd()` skips cryptographic output verification for `AppPublicKey` variant, allowing a single Byzantine participant to forge CKD responses — (`File: crates/contract/src/lib.rs`)

---

### Summary

`respond_ckd()` applies `ckd_output_check` only to the `AppPublicKeyPV` variant of `CKDAppPublicKey`. The `AppPublicKey` (privately-verifiable, legacy) variant receives no on-chain cryptographic verification of the response. Because `respond` (for signatures) implicitly enforces the threshold by verifying the signature against the public key, a single honest node cannot forge a valid signature. But for `AppPublicKey` CKD requests, there is no analogous check, so a single attested Byzantine participant below the signing threshold can call `respond_ckd` with an arbitrary `CKDResponse` and the contract will accept and resolve the yield with the forged data.

---

### Finding Description

In `respond_ckd()`, the match on `request.app_public_key` is:

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

`ckd_output_check` verifies the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, which cryptographically binds the response to the MPC network's master public key and the user's ephemeral key. [2](#0-1) 

For `AppPublicKeyPV`, this check is enforced. For `AppPublicKey`, the arm is an empty block `{}` — the response `(big_y, big_c)` is accepted unconditionally. [3](#0-2) 

The `AppPublicKey` variant is the legacy/default format and is widely used — the CLI, README examples, and tests all use it as the primary path. [4](#0-3) 

By contrast, `respond` for ECDSA/EdDSA verifies the signature against the derived public key before resolving the yield, which means a single node cannot forge a valid signature without threshold cooperation. [5](#0-4) 

---

### Impact Explanation

**Critical — Confidential key derivation output without the required participant authorization.**

The CKD decryption performed by the user's TEE app is:

```
secret = big_c − a · big_y
```

where `a` is the user's ephemeral private key. If an attacker submits `big_y = 0` (the G1 identity point) and `big_c = X` for any attacker-chosen `X`, the user computes `X − a · 0 = X`. The attacker fully controls the value the user derives as their "secret key." The contract resolves the yield with the forged response, and the honest nodes' subsequent `respond_ckd` calls fail because the request is already resolved. The user's TEE app receives and uses an attacker-controlled key, breaking the confidentiality guarantee of the CKD protocol entirely for the `AppPublicKey` variant. [6](#0-5) 

---

### Likelihood Explanation

**Medium.** The attacker must be an attested participant (i.e., a node that has passed TEE attestation and is in the participant set). However, the threshold is not enforced for `AppPublicKey` CKD responses — a single participant suffices. The `AppPublicKey` variant is the default/legacy path used by most callers. The attacker only needs to observe a pending `request_app_private_key` transaction on-chain and race to call `respond_ckd` with a forged response before the honest nodes do. This is a realistic race condition on NEAR, where block times are ~1 second and the attacker runs an indexer. [7](#0-6) 

---

### Recommendation

Apply `ckd_output_check` to the `AppPublicKey` variant as well, or add an equivalent binding check. Since `AppPublicKey` provides only a G1 point (no G2 counterpart for the pairing), the existing `ckd_output_check` cannot be used directly. The fix should either:

1. **Require all new CKD requests to use `AppPublicKeyPV`** (deprecate `AppPublicKey`), so the on-chain pairing check is always enforced; or
2. **Add a threshold-enforced aggregation step** before resolving the yield for `AppPublicKey` requests, so that at least `t` participants must submit the same `(big_y, big_c)` before the contract accepts it.

The analogous fix in the external report's context was to extend the condition to cover the excluded variant. Here the fix must either extend the cryptographic check or enforce threshold agreement at the contract level.

```diff
  match &request.app_public_key {
-     dtos::CKDAppPublicKey::AppPublicKey(_) => {}
+     dtos::CKDAppPublicKey::AppPublicKey(_) => {
+         // AppPublicKey lacks a G2 component for pairing; require AppPublicKeyPV
+         // or enforce threshold aggregation before resolving.
+         env::panic_str("AppPublicKey variant is deprecated; use AppPublicKeyPV for verifiable CKD");
+     }
      dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
          if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
              env::panic_str("CKD output check failed");
          }
      }
  }
```

---

### Proof of Concept

1. User calls `request_app_private_key` with `AppPublicKey(pk1)` where `pk1 = a · G1` for some private `a`.
2. The request is stored as a pending yield in `pending_ckd_requests`.
3. A single Byzantine attested participant observes the pending request on-chain.
4. The attacker calls `respond_ckd(ckd_request, CKDResponse { big_y: G1_IDENTITY, big_c: ATTACKER_CHOSEN_VALUE })`.
5. `respond_ckd` reaches the match arm `AppPublicKey(_) => {}` — no check is performed.
6. `resolve_yields_for` resolves the yield with the forged response.
7. The user's TEE app receives `(big_y=0, big_c=X)` and computes `secret = X − a·0 = X`.
8. The user derives a key from `X`, which the attacker chose and knows.
9. Honest nodes' subsequent `respond_ckd` calls fail (request already resolved). [8](#0-7) [9](#0-8)

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

**File:** crates/contract/src/lib.rs (L654-666)
```rust
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

**File:** crates/contract/src/lib.rs (L675-688)
```rust
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

**File:** crates/ckd-example-cli/src/ckd.rs (L31-34)
```rust
    } else {
        let (scalar, pk) = generate_ephemeral_key(&mut OsRng);
        (scalar, CKDAppPublicKey::AppPublicKey(pk))
    };
```
