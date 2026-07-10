### Title
Unverified `AppPublicKey` CKD Response Allows Single Byzantine Participant to Corrupt User Key Derivation - (File: `crates/contract/src/lib.rs`)

### Summary
`respond_ckd` applies a cryptographic pairing check only to `AppPublicKeyPV` responses. For the `AppPublicKey` (legacy) variant the check branch is empty, so any `CKDResponse` — including all-zero garbage — is accepted from a single attested participant. A Byzantine node strictly below the signing threshold can call `respond_ckd` with arbitrary bytes, permanently resolving the user's yield with an unusable key while the correct threshold-computed output is never delivered.

### Finding Description

In `respond_ckd` (`crates/contract/src/lib.rs` lines 675–682) the contract branches on the request variant:

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

For `AppPublicKeyPV`, `ckd_output_check` (`crates/contract/src/primitives/ckd.rs` lines 80–102) enforces the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`. A single participant cannot satisfy this without the full master secret, so the check enforces the threshold requirement at the contract level. [2](#0-1) 

For `AppPublicKey`, the branch is empty. Any `CKDResponse` with any `big_y` and `big_c` values passes. After the match, `resolve_yields_for` is called unconditionally, resuming all queued yields with the unverified bytes and deleting the pending-request entry. [3](#0-2) 

**Contrast with `respond` (signature responses):** the contract verifies the ECDSA/EdDSA signature against the derived public key before resolving any yield. A single participant cannot forge a valid threshold signature, so the cryptographic check enforces the threshold requirement at the contract level. [4](#0-3) 

For `AppPublicKey` CKD, no equivalent enforcement exists. The existing unit test at lines 3403–3441 confirms this: it passes `big_y=[1u8;48], big_c=[2u8;48]` (arbitrary bytes) and the call succeeds without any validity check. [5](#0-4) 

**Attacker path:**
1. Victim submits `request_app_private_key` with `AppPublicKey` variant and 1 yoctonear deposit.
2. Byzantine attested participant (one node, below threshold) calls `respond_ckd` with `big_y = [0u8; 48]`, `big_c = [0u8; 48]`.
3. Contract passes all guards (`assert_caller_is_attested_participant_and_protocol_active`, `accept_requests`, domain BLS12-381 check) and resolves the yield.
4. Victim receives garbage `(big_y, big_c)` that decrypts to nothing meaningful.
5. The pending request is gone; the victim must re-submit and pay again, only to face the same attack.

The `check_request_preconditions` guard that blocks users when `accept_requests = false` is irrelevant here — the attacker is a node calling `respond_ckd`, not a user calling `sign`. [6](#0-5) 

### Impact Explanation

A single Byzantine participant (below the signing threshold) can permanently fail any `AppPublicKey` CKD request by delivering a garbage response the contract unconditionally accepts. The user's request lifecycle is corrupted: the yield is resolved with unusable data, the pending-request entry is deleted, and the user cannot recover the correct derived key from that request. If the derived key controls application funds or access, the user loses access to those assets. This breaks the production safety invariant that CKD outputs must be cryptographically correct before the contract resolves the user's yield — an invariant that is enforced for every other response type (`respond`, `respond_verify_foreign_tx`, `respond_ckd` with `AppPublicKeyPV`).

### Likelihood Explanation

Any single attested MPC participant can trigger this. The attacker does not need threshold collusion — one node with a valid TEE attestation suffices. The attack is cheap (one contract call per victim request), repeatable for every new request the victim submits, and leaves no on-chain evidence distinguishing it from a legitimate response. The `AppPublicKey` variant is the legacy default path and is actively used in production (the e2e test at `crates/e2e-tests/tests/ckd_verification.rs` lines 40–91 exercises it). [7](#0-6) 

### Recommendation

Add an on-chain cryptographic check for `AppPublicKey` responses. Since the user holds the discrete-log witness (their private key), the contract cannot run the same pairing equation used for `AppPublicKeyPV`. Two mitigations are viable:

1. **Require threshold-signed commitment:** require the response to be accompanied by a BLS aggregate signature from at least `threshold` participants over `(app_id, big_y, big_c)`, verifiable against the stored public key.
2. **Deprecate `AppPublicKey`:** migrate all new CKD requests to `AppPublicKeyPV` (which already has on-chain verification) and reject new `AppPublicKey` submissions, preserving backward compatibility only for already-queued requests.

### Proof of Concept

```rust
// Victim submits CKD request with AppPublicKey (legacy) variant
contract.request_app_private_key(CKDRequestArgs {
    derivation_path: "my/path".to_string(),
    app_public_key: CKDAppPublicKey::AppPublicKey(victim_g1_pk),
    domain_id: bls_domain_id,
});

// Attacker: single Byzantine attested participant, below threshold
// Calls respond_ckd with all-zero garbage — no cryptographic check for AppPublicKey
with_active_participant_and_attested_context(&contract);
contract.respond_ckd(
    ckd_request,
    CKDResponse {
        big_y: Bls12381G1PublicKey([0u8; 48]),  // garbage
        big_c: Bls12381G1PublicKey([0u8; 48]),  // garbage
    }
).expect("accepted without any validity check");

// Yield is resolved, pending request deleted.
// Victim receives (big_y=[0;48], big_c=[0;48]) — undecryptable.
// Victim must re-submit; attacker repeats indefinitely.
```

This is directly demonstrated by the existing test `respond_ckd__should_succeed_when_response_is_valid_and_request_exists` which passes `big_y=[1u8;48], big_c=[2u8;48]` (not a valid CKD output) and the call succeeds — confirming the absence of any output check for the `AppPublicKey` branch. [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L298-302)
```rust
        // 4. Refuse the request if the contract is not currently accepting requests
        //    (e.g. because TEE validation has failed).
        if !self.accept_requests {
            env::panic_str(&TeeError::TeeValidationFailed.to_string())
        }
```

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

**File:** crates/contract/src/lib.rs (L684-688)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_ckd_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
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
