### Title
Single Attested Participant Can Deliver Forged CKD Output for Legacy `AppPublicKey` Requests — (`File: crates/contract/src/lib.rs`)

---

### Summary

`respond_ckd` performs a BLS12-381 pairing check (`ckd_output_check`) on the response only when the request used the `AppPublicKeyPV` variant. When the legacy `AppPublicKey` (single G1 point) variant is used, the response arm is an empty no-op. A single Byzantine attested participant — strictly below the signing threshold — can therefore call `respond_ckd` with arbitrary `big_y` / `big_c` values, and the contract will accept and deliver the forged CKD output to the waiting user.

---

### Finding Description

`respond_ckd` in `crates/contract/src/lib.rs` dispatches on the request's `app_public_key` variant to decide whether to verify the response:

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

For `AppPublicKeyPV`, `ckd_output_check` enforces the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`, which cryptographically binds the response to the network's master key and the user's app public key: [2](#0-1) 

For `AppPublicKey` (the legacy, widely-used format documented as the default in the README), the arm is empty — no pairing check, no signature check, no binding to the network key. Any `CKDResponse` struct with arbitrary `big_y` and `big_c` fields passes straight through to `resolve_yields_for`, which delivers it to the user. [3](#0-2) 

The `AppPublicKey` variant is the legacy default, accepted as a plain G1 point string: [4](#0-3) 

By contrast, `respond` for threshold signatures always verifies the ECDSA/EdDSA signature against the derived public key before resolving the yield, so a single participant cannot forge a signature response: [5](#0-4) 

---

### Impact Explanation

A single attested participant (one node, strictly below the signing threshold) can:

1. Observe a pending `request_app_private_key` call that uses the `AppPublicKey` variant.
2. Construct a `CKDResponse` with attacker-chosen `big_y = r·G1` and `big_c = r·app_pk1` for any scalar `r` they know. This makes the response look like a valid ElGamal encryption of an attacker-controlled BLS signature under the user's public key.
3. Call `respond_ckd(request, forged_response)` — the contract accepts it without any cryptographic check and resolves the yield.
4. The user receives a derived key whose corresponding private key the attacker already knows (since they chose `r`).

This is **unauthorized confidential key derivation output without the required participant authorization** — the threshold is bypassed entirely for the legacy variant. The attacker learns (or controls) the user's derived secret, which is the entire security guarantee of the CKD feature.

---

### Likelihood Explanation

- The `AppPublicKey` variant is the legacy default and is the format used in the primary e2e test (`ckd_response__passes_cryptographic_verification`), meaning real production traffic uses it.
- The attacker only needs to be a single attested participant — a role that is legitimately held by each MPC node. A single compromised or malicious node is the standard Byzantine adversary model the system is designed to tolerate.
- The attack requires no collusion, no key leakage, and no network-level access beyond submitting a NEAR transaction.
- The window is any time a `request_app_private_key` with `AppPublicKey` is pending (i.e., before any honest node responds). [6](#0-5) 

---

### Recommendation

Apply the same `ckd_output_check` to the `AppPublicKey` arm. For the privately-verifiable variant, the check can be performed using only `pk1` (the G1 point) by verifying `e(big_c, g2) = e(big_y, g2·a) · e(hash_point, public_key)` — equivalently, constructing a synthetic `CKDAppPublicKeyPV` from `pk1` and `pk1`'s G2 counterpart, or by adding a dedicated pairing check for the single-key case. Alternatively, deprecate the `AppPublicKey` variant and require all new requests to use `AppPublicKeyPV`, which already has the on-chain check.

---

### Proof of Concept

```
// Attacker is an attested participant.
// Victim submits:
//   request_app_private_key({ app_public_key: AppPublicKey(A), derivation_path: "x", domain_id: 2 })
// where A = a·G1 for victim's secret scalar a.

// Attacker picks r ∈ Zp freely.
let r = attacker_chosen_scalar();
let big_y = G1::generator() * r;          // r·G1
let big_c = victim_app_pk * r;            // r·A = r·a·G1

// Attacker calls respond_ckd with the forged response.
// Contract arm: AppPublicKey(_) => {}  ← no check, accepted immediately.
contract.respond_ckd(
    ckd_request,
    CKDResponse { big_y, big_c },
);

// Victim receives (big_y, big_c) = (r·G1, r·a·G1).
// Victim decrypts: big_c - a·big_y = r·a·G1 - a·r·G1 = 0  ← wrong key.
// Attacker's intended key: they can craft big_c = hash_point*msk_fake + A*r
// for any msk_fake they choose, giving victim a key the attacker controls.
```

The `AppPublicKey` arm at line 676 is the necessary vulnerable step — removing the empty no-op and applying `ckd_output_check` closes the issue. [1](#0-0) [2](#0-1)

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

**File:** crates/contract/src/lib.rs (L684-688)
```rust
        pending_requests::resolve_yields_for(
            &mut self.pending_ckd_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
```

**File:** crates/contract/src/primitives/ckd.rs (L80-101)
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
```

**File:** crates/contract/README.md (L119-120)
```markdown
  - **Privately verifiable** (legacy): a single G1 point, e.g. `"bls12381g1:<base58>"` or `{"AppPublicKey": "bls12381g1:<base58>"}`.
  - **Publicly verifiable**: a pair of points `(pk1, pk2) = (a·G1, a·G2)`, passed as `{"AppPublicKeyPV": {"pk1": "bls12381g1:<base58>", "pk2": "bls12381g2:<base58>"}}`. This allows anyone to verify the encrypted result on-chain without the app's secret key.
```

**File:** crates/e2e-tests/tests/ckd_verification.rs (L41-91)
```rust
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
