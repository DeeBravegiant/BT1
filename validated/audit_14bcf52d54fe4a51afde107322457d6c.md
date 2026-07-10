### Title
Missing Cryptographic Verification of CKD Response for `AppPublicKey` Variant Allows Byzantine Participant to Inject Attacker-Controlled Derived Key - (File: `crates/contract/src/lib.rs`)

### Summary

`respond_ckd` performs no on-chain cryptographic verification of the `CKDResponse` when the original request used the `AppPublicKey` (privately-verifiable, legacy) variant. A single Byzantine attested participant — strictly below the signing threshold — can race-submit an arbitrary `CKDResponse` that the contract accepts unconditionally, delivering an attacker-controlled derived key to the user.

### Finding Description

In `respond_ckd`, the contract branches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no check whatsoever
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` enforces the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, which cryptographically binds the response to the MPC master key and the user's public key. [2](#0-1) 

For `AppPublicKey`, the arm is an empty block. The response bytes are serialized and passed directly to `resolve_yields_for`, which resumes the user's yield with whatever the caller submitted. [3](#0-2) 

The CKD ElGamal decryption the user performs is: `derived_key = big_c − a · big_y`, where `a` is the user's private scalar. An attacker who submits `big_y = identity_point` and `big_c = attacker_scalar · G1` causes the user to compute `attacker_scalar · G1 − a · identity = attacker_scalar · G1`. The attacker chose `attacker_scalar`, so they know the user's resulting derived key.

The `respond` and `respond_verify_foreign_tx` functions do not have this gap: both verify the submitted signature cryptographically before resolving the yield. [4](#0-3) 

### Impact Explanation

A Byzantine participant who wins the race to call `respond_ckd` delivers an attacker-chosen G1 point as the user's derived private key. The user's application then uses this key for signing or decryption, operations the attacker can fully replicate because they know the key. This constitutes unauthorized confidential key derivation output without the required threshold-participant authorization — matching the Critical impact class: *"Confidential key derivation output without the required participant authorization."*

The legitimate MPC response, when it arrives, fails with `RequestNotFound` because the yield was already consumed, so the user cannot recover the correct key.

### Likelihood Explanation

Any single attested participant (below the signing threshold) can execute this attack. The attacker monitors the NEAR chain for `request_app_private_key` transactions using the `AppPublicKey` variant, then immediately submits a crafted `respond_ckd`. Because NEAR transaction ordering within a block is deterministic and the attacker controls their own submission timing, front-running is straightforward. No collusion, no leaked keys, and no privileged operator access are required.

### Recommendation

Apply the same pairing-based output check to the `AppPublicKey` branch, or reject `AppPublicKey` requests entirely in favour of the publicly-verifiable `AppPublicKeyPV` variant, which already has the correct guard. At minimum, add a note in the contract that `AppPublicKey` responses are unverified and document the trust assumption this places on the participant set.

### Proof of Concept

1. User calls `request_app_private_key` with `AppPublicKey(app_pk1 = a·G1)` (user's private scalar `a`).
2. Byzantine attested participant immediately calls `respond_ckd` with:
   - `big_y = identity_point` (compressed identity bytes for BLS12-381 G1)
   - `big_c = attacker_scalar · G1` (attacker picks `attacker_scalar`)
3. Contract executes the empty `AppPublicKey` branch — no check — and calls `resolve_yields_for`, resuming the user's yield with the malicious response. [5](#0-4) 
4. User's yield-callback `return_ck_and_clean_state_on_success` returns `Ok(response)` directly to the caller. [6](#0-5) 
5. User decrypts: `big_c − a · big_y = attacker_scalar·G1 − a·identity = attacker_scalar·G1`.
6. Attacker knows `attacker_scalar`, so they know the user's derived key and can forge any signature or decrypt any ciphertext produced with it.
7. The legitimate MPC response arrives and fails with `RequestNotFound` — the user cannot obtain the correct key. [7](#0-6)

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

**File:** crates/contract/src/lib.rs (L2283-2289)
```rust
    pub fn return_ck_and_clean_state_on_success(
        &mut self,
        request: CKDRequest,
        #[callback_result] ck: Result<CKDResponse, PromiseError>,
    ) -> PromiseOrValue<CKDResponse> {
        match ck {
            Ok(ck) => PromiseOrValue::Value(ck),
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

**File:** crates/contract/src/pending_requests.rs (L74-87)
```rust
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
```
