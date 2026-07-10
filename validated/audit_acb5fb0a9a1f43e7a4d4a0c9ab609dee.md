### Title
Unverified CKD Response for `AppPublicKey` Variant Allows Single Byzantine Participant to Forge Key Derivation Output - (File: crates/contract/src/lib.rs)

### Summary

`respond_ckd` in `MpcContract` applies cryptographic output verification only for the `AppPublicKeyPV` variant of a CKD request, but performs **no verification whatsoever** for the `AppPublicKey` variant. A single attested participant — strictly below the signing threshold — can call `respond_ckd` with an arbitrary forged `CKDResponse` for any pending `AppPublicKey`-type request, and the contract will accept and deliver it to the user. This bypasses the threshold requirement for confidential key derivation.

### Finding Description

`respond_ckd` contains an asymmetric validation pattern:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← NO verification
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` cryptographically verifies that the submitted `CKDResponse` (`big_y`, `big_c`) is consistent with the BLS12-381 master public key and the request's `app_id`. For `AppPublicKey`, the arm is a no-op — any `big_y` and `big_c` values are accepted unconditionally.

The contrast with `respond` (for regular sign requests) is instructive: `respond` always derives the expected public key from the root key plus the request's tweak and verifies the signature against it before accepting the response. [2](#0-1) 

No equivalent check exists for the `AppPublicKey` CKD path.

The only guards in `respond_ckd` before reaching `resolve_yields_for` are:
1. Caller must be the signer account (`assert_caller_is_signer`)
2. Protocol must be running or resharing
3. `accept_requests` must be true
4. Caller must be an attested participant (`assert_caller_is_attested_participant_and_protocol_active`) [3](#0-2) 

None of these guards verify that the submitted `CKDResponse` is the correct threshold-computed output for the pending request.

### Impact Explanation

A single Byzantine attested participant (strictly below the signing threshold) can:

1. Observe a pending `AppPublicKey`-type CKD request in `pending_ckd_requests`.
2. Construct an arbitrary `CKDResponse` with any `big_y` and `big_c` values.
3. Call `respond_ckd` with the forged response.
4. The contract accepts it, calls `resolve_yields_for`, and delivers the forged key material to the waiting user via `promise_yield_resume`. [4](#0-3) 

The user receives fabricated CKD output — either garbage (rendering their derived key unusable) or, if the attacker is sophisticated, key material the attacker controls. This is an unauthorized confidential key derivation output delivered without the required threshold participant authorization, matching the Critical impact category.

### Likelihood Explanation

The `AppPublicKey` variant is the legacy/default CKD path and is actively used in production. Any single attested participant who turns Byzantine can exploit this immediately upon observing a pending request. No collusion above threshold is required — one participant suffices. The attacker-controlled entry path is the public `respond_ckd` contract method, callable by any attested participant. [5](#0-4) 

### Recommendation

Apply the same cryptographic output verification to the `AppPublicKey` variant that is already applied to `AppPublicKeyPV`. Specifically, add a verification step for `AppPublicKey` responses that confirms the submitted `big_y` and `big_c` are consistent with the BLS12-381 master public key and the request parameters before calling `resolve_yields_for`. If no such check is feasible for the legacy variant (because the user did not supply a provably-valid app public key), consider deprecating `AppPublicKey` in favor of `AppPublicKeyPV` exclusively, or requiring threshold-level consensus before the contract accepts a CKD response.

### Proof of Concept

1. User calls `request_app_private_key` with `CKDAppPublicKey::AppPublicKey(some_bls_pk)`. The request is queued in `pending_ckd_requests`. [6](#0-5) 

2. A single attested participant (e.g., participant index 0) constructs a forged response:
   ```rust
   let forged_response = CKDResponse {
       big_y: Bls12381G1PublicKey([0xde; 48]),  // arbitrary garbage
       big_c: Bls12381G1PublicKey([0xad; 48]),
   };
   ```
3. The participant calls `respond_ckd(ckd_request, forged_response)`.
4. The `AppPublicKey` arm is a no-op — no verification occurs.
5. `resolve_yields_for` resumes the user's yield with the forged bytes.
6. The user's `request_app_private_key` call returns the forged `CKDResponse` as if it were the legitimate threshold-computed output.

The existing test `respond_ckd__should_succeed_when_response_is_valid_and_request_exists` demonstrates that a single participant can successfully call `respond_ckd` with arbitrary `big_y`/`big_c` values (`[1u8; 48]` and `[2u8; 48]`) for an `AppPublicKey` request, confirming the absence of any output check. [7](#0-6)

### Citations

**File:** crates/contract/src/lib.rs (L484-511)
```rust
        match &request.app_public_key {
            dtos::CKDAppPublicKey::AppPublicKey(_) => {}
            dtos::CKDAppPublicKey::AppPublicKeyPV(pk) => {
                if !app_public_key_check(pk) {
                    env::panic_str("app public key check failed")
                }
            }
        }

        let request = CKDRequest::new(
            request.app_public_key,
            domain_id,
            &predecessor,
            &request.derivation_path,
        );

        let callback_gas = Gas::from_tgas(
            self.config
                .return_ck_and_clean_state_on_success_call_tera_gas,
        );

        let callback_args = serde_json::to_vec(&(&request,)).unwrap();
        self.enqueue_yield_request(
            method_names::RETURN_CK_AND_CLEAN_STATE_ON_SUCCESS,
            callback_args,
            callback_gas,
            move |this, id| this.add_ckd_request(request, id),
        );
```

**File:** crates/contract/src/lib.rs (L596-608)
```rust
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
