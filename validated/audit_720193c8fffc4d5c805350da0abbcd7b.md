### Title
Unvalidated CKD Response for `AppPublicKey` Variant Allows Single Byzantine Node to Corrupt Confidential Key Derivation Output - (File: crates/contract/src/lib.rs)

### Summary

In `respond_ckd`, when the request uses the `CKDAppPublicKey::AppPublicKey` variant, the contract performs **no cryptographic validation** of the `CKDResponse`. A single attested participant (Byzantine node below threshold) can submit arbitrary `big_y` and `big_c` values, which the contract unconditionally accepts and delivers to all waiting callers. This is the direct analog of the LineaRollup bug: a specific code path (`AppPublicKey` branch, analogous to `submissionDataLength == 0`) skips the output check that exists in the other branch (`AppPublicKeyPV`).

### Finding Description

In `respond_ckd` at `crates/contract/src/lib.rs:675-682`:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← NO CHECK
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

The `AppPublicKeyPV` branch calls `ckd_output_check`, which verifies via a BLS12-381 pairing that `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`. The `AppPublicKey` branch does nothing. After this match, `resolve_yields_for` is called unconditionally, delivering whatever `big_y`/`big_c` the caller supplied to every queued yield for that request.

The `AppPublicKey` variant is the legacy "privately verifiable" form — the contract cannot perform the pairing check because it only has a single G1 point (`pk1`) and not the G2 counterpart (`pk2`) needed for the pairing equation. However, the absence of an on-chain check does not mean no check is possible: the prover could be required to include a zero-knowledge proof of correct computation, or the protocol could mandate `AppPublicKeyPV` for all new requests. As it stands, the `AppPublicKey` path is entirely trust-based. [1](#0-0) [2](#0-1) 

### Impact Explanation

A single Byzantine attested participant races to call `respond_ckd` before honest nodes, supplying crafted `big_y` and `big_c` values. `resolve_yields_for` drains **all** queued yields for that request key in one call, so every user who submitted the same `request_app_private_key` call receives the corrupted output. The user decrypts `big_c − sk1 · big_y` and obtains a G1 point that is not the correct `big_s = H(pk, app_id) · msk`. Any wallet or application key derived from this wrong value is permanently inaccessible or controlled by no one, breaking the safety invariant of the CKD protocol. This maps to the **Medium** allowed impact: request-lifecycle and contract execution-flow manipulation that breaks production safety/accounting invariants without requiring network-level DoS or operator misconfiguration. [3](#0-2) 

### Likelihood Explanation

The `AppPublicKey` variant is the legacy path still accepted by the contract. Any single attested participant — one node below the signing threshold — can exploit this by monitoring the NEAR chain for `request_app_private_key` calls using `AppPublicKey` and immediately calling `respond_ckd` with arbitrary values. No threshold collusion, no key leakage, and no privileged access are required beyond being an attested participant. [4](#0-3) 

### Recommendation

1. **Deprecate and reject `AppPublicKey` in `respond_ckd`**: Require all new CKD requests to use `AppPublicKeyPV`, which supports the on-chain pairing check. Reject `respond_ckd` calls for `AppPublicKey` requests with an explicit error, or migrate existing callers.
2. **If `AppPublicKey` must remain**: Require the responding node to include a zero-knowledge proof of correct computation (e.g., a Schnorr proof that `big_c − big_y · sk = H(pk, app_id) · msk`) that the contract can verify without the user's secret key.
3. **Mirror the `AppPublicKeyPV` guard**: At minimum, add a comment and a TODO that the `AppPublicKey` branch is intentionally unverified and document the trust assumption explicitly, so future auditors and developers are aware. [1](#0-0) [5](#0-4) 

### Proof of Concept

1. Alice calls `request_app_private_key` with `AppPublicKey(pk1)` on a BLS12-381 domain. The contract stores the pending yield.
2. Mallory (a single attested participant, below threshold) observes the pending request on-chain.
3. Mallory calls `respond_ckd(ckd_request, CKDResponse { big_y: [1u8;48].into(), big_c: [2u8;48].into() })`.
4. The contract enters the `AppPublicKey(_) => {}` branch — no check is performed.
5. `resolve_yields_for` resolves Alice's yield with the fraudulent `(big_y, big_c)`.
6. Alice receives `big_c − sk1 · big_y` as her "confidential key", which is an arbitrary G1 point unrelated to the MPC master secret.
7. Any funds or data Alice protects with this derived key are permanently inaccessible.

This is directly confirmed by the existing unit test `respond_ckd__should_succeed_when_response_is_valid_and_request_exists`, which passes `big_y: [1u8;48]` and `big_c: [2u8;48]` (clearly not a valid protocol output) and the contract accepts it without error. [6](#0-5)

### Citations

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

**File:** crates/contract/src/primitives/ckd.rs (L61-74)
```rust
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
