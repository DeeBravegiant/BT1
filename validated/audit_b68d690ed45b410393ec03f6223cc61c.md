### Title
Unchecked CKD Response for `AppPublicKey` Variant Allows Byzantine Participant to Inject Arbitrary Key Material - (File: crates/contract/src/lib.rs)

### Summary

In `respond_ckd`, the contract applies a full cryptographic pairing check (`ckd_output_check`) only for the `AppPublicKeyPV` variant of a CKD response. The `AppPublicKey` (legacy, privately-verifiable) variant receives **no validation whatsoever** — any arbitrary `big_y` and `big_c` bytes are accepted and immediately resolved to all waiting callers. A single Byzantine attested participant can exploit this to inject a crafted response that makes the user's derived secret equal to an attacker-known value, effectively stealing the confidential key derivation output.

### Finding Description

In `crates/contract/src/lib.rs`, `respond_ckd` dispatches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← no check at all
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

The `AppPublicKeyPV` arm calls `ckd_output_check`, which verifies the pairing equation `e(big_c, G2) = e(big_y, app_pk2) · e(hash_point, public_key)` using the host's BLS12-381 pairing. [2](#0-1) 

The `AppPublicKey` arm is completely empty. No point-validity check, no pairing check, no structural check. The response is passed directly to `pending_requests::resolve_yields_for`, which fans the raw bytes out to every queued yield for that request. [3](#0-2) 

This is confirmed by the existing unit test, which passes `[1u8; 48]` and `[2u8; 48]` — bytes that are not valid compressed G1 points — as `big_y` and `big_c` for an `AppPublicKey` request, and the contract accepts them without complaint: [4](#0-3) 

### Impact Explanation

The CKD protocol produces `big_c = H(pk ‖ app_id)·msk + app_pk·y` and `big_y = G1·y`. The user recovers the secret as `big_s = big_c − a·big_y`, where `a` is their private scalar (`app_pk = a·G1`).

If an attacker submits `big_y = 0` (the G1 identity point, a valid compressed encoding) and `big_c = G1·k` for any attacker-chosen scalar `k`, the user computes:

```
big_s = big_c − a · 0 = big_c = G1·k
```

The attacker knows `k`, so they know `big_s`. The user has no way to detect this: they do not know `msk`, so they cannot verify that `big_s` equals `H(pk ‖ app_id)·msk`. They use the attacker-controlled `big_s` as their derived application secret. Any funds or credentials protected by that derived key are now accessible to the attacker.

Because `resolve_yields_for` drains the entire fan-out queue in one call, every user who submitted the same `AppPublicKey` CKD request concurrently receives the same poisoned response.

**Impact class**: Critical — confidential key derivation output is stolen by a single Byzantine participant without threshold collusion.

### Likelihood Explanation

`respond_ckd` requires only that the caller is an attested participant (`assert_caller_is_attested_participant_and_protocol_active`). [5](#0-4)  There is no threshold requirement on the respond path — a single node suffices. TEE attestation proves the node is running the expected image, but it does not prevent a Byzantine node from calling `respond_ckd` with a crafted payload. The `AppPublicKey` variant is the legacy default and is actively used in production requests. [6](#0-5) 

### Recommendation

Add a minimum structural check for the `AppPublicKey` arm: at least verify that `big_y` and `big_c` are valid, non-identity G1 points using `env::bls12381_p1_decompress` (which aborts on malformed encodings) and an explicit identity-point rejection. [7](#0-6) 

Longer term, consider requiring callers to use `AppPublicKeyPV` for all new requests so the full pairing check is always enforced, and deprecate the unverifiable `AppPublicKey` variant.

### Proof of Concept

1. User submits `request_app_private_key` with `AppPublicKey(pk1)` where `pk1 = a·G1`.
2. Byzantine participant constructs `big_y = compressed(G1_identity)` and `big_c = compressed(G1·k)` for known scalar `k`.
3. Participant calls `respond_ckd(request, CKDResponse { big_y, big_c })`.
4. Contract enters the `AppPublicKey(_) => {}` arm — no check — and resolves all pending yields with the crafted bytes.
5. User's client decompresses `big_y` to the identity, computes `big_s = big_c − a·0 = G1·k`.
6. Attacker knows `k`, therefore knows `big_s`, and can derive the same application secret as the user.

The existing test at `crates/contract/src/lib.rs:3403–3441` already demonstrates that the contract accepts completely arbitrary bytes for `big_y`/`big_c` in the `AppPublicKey` path with no error. [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L666-666)
```rust
        self.assert_caller_is_attested_participant_and_protocol_active();
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

**File:** crates/contract/src/primitives/ckd.rs (L56-74)
```rust
/// Check that `e(app_pk1, g2) = e(g1, app_pk2)`.
///
/// Point validation is fully delegated to the host: the decompression
/// functions abort execution on malformed or off-curve encodings, and
/// `bls12381_pairing_check` returns `false` when a point is outside its
/// prime-order subgroup.
pub(crate) fn app_public_key_check(app_public_key: &dtos::CKDAppPublicKeyPV) -> bool {
    let pk1 = env::bls12381_p1_decompress(&app_public_key.pk1);
    let pk2 = env::bls12381_p2_decompress(&app_public_key.pk2);

    let pairing_input = [
        pk1.as_slice(),
        MINUS_G2_GENERATOR_UNCOMPRESSED.as_slice(),
        G1_GENERATOR_UNCOMPRESSED.as_slice(),
        pk2.as_slice(),
    ]
    .concat();
    env::bls12381_pairing_check(&pairing_input)
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
