### Title
Unverified CKD Response in `respond_ckd` for `AppPublicKey` Variant Allows Single Byzantine Participant to Forge Key Derivation Output - (File: crates/contract/src/lib.rs)

### Summary

The `respond_ckd` function in `crates/contract/src/lib.rs` performs no cryptographic verification of the `CKDResponse` when the request uses the `AppPublicKey` (legacy, privately-verifiable) variant. A single Byzantine attested participant can call `respond_ckd` with an arbitrary forged `(big_y, big_c)` pair, which the contract accepts and delivers to the user without any check that the response is consistent with the MPC master public key. This bypasses the threshold requirement for confidential key derivation.

### Finding Description

The `respond_ckd` function handles two variants of `CKDAppPublicKey`:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← NO VERIFICATION
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
``` [1](#0-0) 

For the `AppPublicKeyPV` variant, the contract calls `ckd_output_check`, which verifies the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`. This cryptographically binds the response to the MPC master public key, ensuring the response could only have been produced by a legitimate threshold computation. [2](#0-1) 

For the `AppPublicKey` variant, the empty arm `{}` means the contract accepts any `(big_y, big_c)` values without any check. The response is immediately serialized and used to resume all queued yields for that request.

The analogous check for signatures in `respond` always verifies the signature cryptographically: [3](#0-2) 

This means signatures cannot be forged by a single participant (the verification would fail), but CKD responses with `AppPublicKey` can be.

### Impact Explanation

A single Byzantine attested participant can:

1. Observe a pending `CKDRequest` with `AppPublicKey` variant in `pending_ckd_requests`.
2. Call `respond_ckd` with an arbitrary `CKDResponse { big_y: <any>, big_c: <any> }`.
3. The contract accepts the forged response, resolves all queued yields, and delivers the forged `(big_y, big_c)` to every caller who submitted that request.

The user receives a `CKDResponse` that is not derived from the MPC master secret via the threshold protocol. The threshold requirement (t-of-n) is completely bypassed for this code path. This constitutes unauthorized confidential key derivation output without the required participant authorization.

Additionally, since `resolve_yields_for` drains the entire fan-out queue, all duplicate submissions of the same request receive the forged response, and the request is permanently consumed — honest nodes can no longer respond. [4](#0-3) 

### Likelihood Explanation

- The attacker must be an attested MPC participant (below the signing threshold).
- The `AppPublicKey` variant is the legacy/default variant and is actively used in production (documented in the README and tested in e2e tests).
- The attacker only needs to submit `respond_ckd` before honest nodes do. Since MPC nodes race to submit responses, a Byzantine node can attempt to win this race.
- No special knowledge is required beyond being an attested participant and observing the on-chain pending request.

### Recommendation

Apply the same cryptographic verification to the `AppPublicKey` branch. For the privately-verifiable variant, the contract cannot use the pairing-based `ckd_output_check` (which requires `pk2`), but it can verify the response using the user's `pk1` and the master public key via an alternative check. At minimum, the contract should verify that `big_c` and `big_y` are valid points on the BLS12-381 G1 curve and that the response is consistent with the master public key.

Alternatively, require all new CKD requests to use `AppPublicKeyPV` (which already has verification) and deprecate the unverified `AppPublicKey` path.

### Proof of Concept

1. Alice submits `request_app_private_key` with `AppPublicKey(alice_pk)` and `domain_id = 2` (BLS domain).
2. The request is stored in `pending_ckd_requests`.
3. Byzantine participant Eve (attested, but below threshold) calls:
   ```
   respond_ckd(
     request = <alice's CKDRequest>,
     response = CKDResponse { big_y: G1::generator(), big_c: G1::generator() }
   )
   ```
4. The contract executes the `AppPublicKey(_) => {}` branch — no check.
5. `resolve_yields_for` resumes Alice's yield with the forged response.
6. Alice receives `(big_y=G, big_c=G)` — not derived from the MPC master secret.
7. Alice decrypts `big_c - alice_sk * big_y = G - alice_sk * G`, which is a value unrelated to the correct derived key.
8. The honest MPC nodes' subsequent `respond_ckd` calls return `Err(RequestNotFound)` because the queue was already drained. [5](#0-4)

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
