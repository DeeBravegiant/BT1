### Title
Unverified CKD Response for `AppPublicKey` Variant Allows Single Malicious Participant to Inject Arbitrary Confidential Key Material - (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_ckd` in the MPC contract performs cryptographic output verification only for the `AppPublicKeyPV` (publicly verifiable) variant of CKD requests. For the `AppPublicKey` (privately verifiable) variant — the standard, more commonly used path — the contract accepts and delivers any arbitrary `CKDResponse` without any on-chain verification. A single malicious attested participant (strictly below the signing threshold) can call `respond_ckd` with a fabricated `big_y`/`big_c` pair and the contract will resolve the user's yield with that fake output.

---

### Finding Description

In `respond_ckd`, after confirming the caller is an attested participant, the contract branches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← no verification
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` enforces the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, which cryptographically proves the response was computed from the MPC master secret key. [2](#0-1) 

For `AppPublicKey`, the arm is a no-op (`{}`). The response is immediately serialized and used to resolve all queued yields: [3](#0-2) 

The existing unit test for this path confirms the absence of verification — it passes completely invalid BLS12-381 byte sequences (`[1u8; 48]`, `[2u8; 48]`) as the response and the call succeeds: [4](#0-3) 

By contrast, `respond` (for ECDSA/EdDSA) always verifies the signature against the derived public key before resolving yields: [5](#0-4) 

The CKD protocol documentation confirms that for the privately verifiable variant, "verification is performed by the app after decryption." However, the app receives `big_c` and `big_y` and computes `k = big_c − big_y · a`. Without knowing the MPC master secret key `msk`, the app cannot verify that `k = msk · H(app_id) + a · y`. The app has no means to detect a forged response. [6](#0-5) 

---

### Impact Explanation

A single malicious attested MPC participant (strictly below the signing threshold) can call `respond_ckd` with an arbitrary `CKDResponse` for any pending `AppPublicKey` CKD request. The contract resolves the user's yield with the fabricated output. The user's TEE application receives a wrong encrypted key, decrypts it to an attacker-influenced value, and uses that value as its deterministic secret — breaking the fundamental confidentiality and correctness guarantee of the CKD feature. Neither the contract nor the TEE app can detect the substitution.

This maps to: **Medium — request-lifecycle and contract execution-flow manipulation that breaks production safety/accounting invariants** (the invariant being that a CKD response delivered to a user must be a valid encryption of `msk · H(app_id)` under the user's public key).

---

### Likelihood Explanation

The attacker must be a single attested MPC participant — a condition strictly below the threshold. The attacker must race the honest leader to call `respond_ckd` first for a target request. Because the contract resolves the yield on the first valid `respond_ckd` call and drains the entire fan-out queue, a single early call wins. The `AppPublicKey` variant is the standard (non-PV) CKD path and is the more commonly used variant, making it the higher-value target.

---

### Recommendation

Apply the same on-chain cryptographic verification to the `AppPublicKey` branch that is already applied to `AppPublicKeyPV`. For the privately verifiable variant, a verification analogous to `ckd_output_check` can be constructed using only the G2 network public key and the G1 app public key (without requiring the app's G2 key). Alternatively, require all CKD requests to use the `AppPublicKeyPV` variant so the pairing check is always enforced on-chain before the response is delivered.

---

### Proof of Concept

1. User submits `request_app_private_key` with `CKDAppPublicKey::AppPublicKey(pk)` for domain `d`.
2. Contract stores the pending yield under the `CKDRequest` key.
3. Malicious attested participant calls `respond_ckd(ckd_request, CKDResponse { big_y: [0u8;48], big_c: [0u8;48] })` — fabricated, cryptographically invalid values.
4. `respond_ckd` passes all checks (attested participant, running state, domain type) and reaches the `AppPublicKey` branch, which is a no-op.
5. `resolve_yields_for` fires `promise_yield_resume` with the fabricated response for every queued yield.
6. The user's TEE app receives `big_y = 0`, `big_c = 0`, decrypts to the identity element, and uses it as its deterministic secret — an attacker-controlled value.

The unit test at `crates/contract/src/lib.rs:3403–3441` already demonstrates this path succeeds with `[1u8;48]`/`[2u8;48]` (invalid BLS points), confirming no verification occurs. [7](#0-6)

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

**File:** crates/contract/src/lib.rs (L675-682)
```rust
        match &request.app_public_key {
            dtos::CKDAppPublicKey::AppPublicKey(_) => {}
            dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
                if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
                    env::panic_str("CKD output check failed");
                }
            }
        }
```

**File:** crates/contract/src/lib.rs (L684-689)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_ckd_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
    }
```

**File:** crates/contract/src/lib.rs (L3403-3441)
```rust
    #[test]
    fn respond_ckd__should_succeed_when_response_is_valid_and_request_exists() {
        let (context, mut contract, _secret_key) = basic_setup(Curve::Bls12381, &mut OsRng);
        let app_public_key: dtos::Bls12381G1PublicKey =
            "bls12381g1:6KtVVcAAGacrjNGePN8bp3KV6fYGrw1rFsyc7cVJCqR16Zc2ZFg3HX3hSZxSfv1oH6"
                .parse()
                .unwrap();
        let request = CKDRequestArgs {
            derivation_path: "".to_string(),
            app_public_key: CKDAppPublicKey::AppPublicKey(app_public_key.clone()),
            domain_id: dtos::DomainId::default(),
        };
        let ckd_request = CKDRequest::new(
            CKDAppPublicKey::AppPublicKey(app_public_key),
            request.domain_id,
            &context.predecessor_account_id,
            &request.derivation_path,
        );
        contract.request_app_private_key(request);
        contract.get_pending_ckd_request(&ckd_request).unwrap();

        let response = CKDResponse {
            big_y: dtos::Bls12381G1PublicKey([1u8; 48]),
            big_c: dtos::Bls12381G1PublicKey([2u8; 48]),
        };

        with_active_participant_and_attested_context(&contract);

        match contract.respond_ckd(ckd_request.clone(), response.clone()) {
            Ok(_) => {
                contract
                    .return_ck_and_clean_state_on_success(ckd_request.clone(), Ok(response))
                    .detach();

                assert!(contract.get_pending_ckd_request(&ckd_request).is_none(),);
            }
            Err(_) => panic!("respond_ckd should not fail"),
        }
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

**File:** crates/threshold-signatures/docs/confidential_key_derivation/confidential-key-derivation.md (L39-47)
```markdown
Two variants of the protocol are supported:

- **Privately verifiable**: Verification is performed by the app after decryption.
- **Publicly verifiable**: Extends the previous variant by allowing any observer
  to verify correctness of the encrypted signature with respect to the MPC
  network public key, without knowing the app's secret key $a$.

The algorithm description below covers both variants. Steps that only apply to
the publicly verifiable variant are marked in blockquotes.
```
