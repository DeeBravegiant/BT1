### Title
Unverified CKD Response for `AppPublicKey` Variant Allows Single Attested Participant to Deliver Fabricated Key Derivation Output - (File: `crates/contract/src/lib.rs`)

### Summary
`respond_ckd` performs no cryptographic verification of the `CKDResponse` when the request uses the `AppPublicKey` variant. A single malicious attested participant (strictly below the signing threshold) can call `respond_ckd` with arbitrary `big_y` and `big_c` values, and the contract will accept and deliver the fabricated output to the waiting user — bypassing the threshold authorization requirement entirely.

### Finding Description
In `respond_ckd`, the contract branches on the `app_public_key` variant of the pending `CKDRequest`:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← no verification whatsoever
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For the `AppPublicKeyPV` variant, `ckd_output_check` verifies the BLS12-381 pairing relationship between the response ciphertext, the commitment, and the master public key — ensuring the response is the genuine threshold-derived output. For the `AppPublicKey` (legacy) variant, the branch is an empty no-op: the contract unconditionally accepts whatever `big_y` and `big_c` the caller supplies.

This is the direct analog of the ERC4626 bug: instead of trusting a vault's `deposit()` return value rather than checking the actual balance change, here the contract trusts the response value provided by a single MPC node rather than independently verifying it against the actual threshold-computed output.

The `AppPublicKey` variant is still actively used and tested: [2](#0-1) 

After the empty branch, `pending_requests::resolve_yields_for` is called unconditionally, which resumes the yield and delivers the unverified response to the original caller: [3](#0-2) 

### Impact Explanation
A single malicious attested participant can supply an arbitrary `CKDResponse { big_y, big_c }` — for example, encrypting a key of their own choosing to the user's `app_public_key` (which is public). The user receives and decrypts a key that was not produced by the threshold MPC protocol. This constitutes **unauthorized confidential key derivation output without the required participant authorization**, matching the Critical impact class: *"Unauthorized… confidential key derivation output without the required participant authorization."*

The `AppPublicKey` variant is the legacy path and is still reachable by any user calling `request_app_private_key`. Any application that relies on the returned key being the genuine MPC-derived secret is silently compromised.

### Likelihood Explanation
The attacker must be an attested participant — a role that requires submitting a valid TEE attestation. However, this is a single-participant attack (1-of-N), strictly below the signing threshold. The entry path is fully reachable from the production contract API with no additional privileges beyond attestation. The `AppPublicKey` variant is not deprecated and remains the default legacy path.

### Recommendation
Apply the same `ckd_output_check` verification to the `AppPublicKey` variant, or derive a verifiable commitment from the BLS master public key and the request parameters that can be checked on-chain without the G2 key. At minimum, require the `AppPublicKeyPV` variant for all new requests and reject `AppPublicKey` submissions, forcing callers to use the publicly-verifiable path.

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(app_pk) => {
        // Require callers to migrate to AppPublicKeyPV, or add
        // an equivalent single-key verification here.
        env::panic_str("AppPublicKey variant is not verifiable; use AppPublicKeyPV");
    }
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

### Proof of Concept
1. User calls `request_app_private_key` with `CKDAppPublicKey::AppPublicKey(user_pk)` and attaches 1 yoctoNEAR. The request is stored in `pending_ckd_requests`.
2. Malicious attested participant constructs a `CKDResponse` with fabricated `big_y` (e.g., an ElGamal encryption of an attacker-chosen scalar under `user_pk`) and a random `big_c`.
3. Malicious participant calls `respond_ckd(ckd_request, fabricated_response)`. The `AppPublicKey` branch is a no-op; no verification runs.
4. `resolve_yields_for` resumes the yield and delivers the fabricated response to the user.
5. The user's application decrypts `big_y` and uses the attacker-chosen key, believing it to be the genuine MPC-derived secret — with no on-chain evidence of tampering. [4](#0-3)

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
