### Title
Unvalidated CKD Response for `AppPublicKey` Variant Allows Single Byzantine Participant to Corrupt Derived Key Material - (File: `crates/contract/src/lib.rs`)

### Summary

The `respond_ckd` function in `MpcContract` applies cryptographic output verification only when the request uses the `AppPublicKeyPV` (publicly verifiable) variant. When the legacy `AppPublicKey` (privately verifiable) variant is used, the response is accepted unconditionally — any `(big_y, big_c)` pair passes. A single Byzantine attested participant (strictly below the signing threshold) can race to submit a fabricated `CKDResponse` for any pending non-PV CKD request, permanently delivering corrupted key material to the requesting application.

### Finding Description

In `respond_ckd`, the contract branches on the `app_public_key` variant of the stored `CKDRequest`:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← no validation whatsoever
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, `ckd_output_check` verifies the BLS12-381 pairing relationship between `(big_y, big_c)`, the `app_id`, and the MPC network public key, ensuring the response is a valid encryption of the correct secret. For `AppPublicKey`, the arm is empty — the response bytes are accepted as-is and immediately forwarded to the caller via `resolve_yields_for`. [2](#0-1) 

The `respond_ckd` function only requires the caller to be a single attested participant: [3](#0-2) 

There is no threshold-of-participants agreement required before the response is accepted and the yield resolved. The first attested participant to call `respond_ckd` with a matching `CKDRequest` key wins, regardless of whether the `(big_y, big_c)` values are cryptographically correct.

This is structurally identical to the reported `uniTransferFrom` analog: when the token is ETH, the `from`/`to` parameters are silently ignored; here, when the key type is `AppPublicKey`, the response content is silently ignored.

### Impact Explanation

**Critical.** A single Byzantine attested participant can permanently corrupt the confidential key derivation output for any pending `AppPublicKey` CKD request. The requesting TEE application receives `(big_y, big_c)` chosen by the attacker. When the app computes `S = big_c − a·big_y`, it recovers attacker-controlled garbage instead of the legitimate MPC-derived secret. Because the yield is resolved and the pending request entry removed, the legitimate response can never be delivered — the app's derived key is permanently lost or replaced with attacker-controlled material. This constitutes unauthorized confidential key derivation output without the required threshold participant authorization. [4](#0-3) 

### Likelihood Explanation

**Medium.** The attacker must be an active, TEE-attested MPC participant — a meaningful barrier. However, only **one** such participant needs to be Byzantine (well below the signing threshold). The attack window is the time between a `request_app_private_key` transaction landing on-chain and the honest nodes submitting their legitimate `respond_ckd`. A malicious participant monitoring the chain can front-run honest nodes. The `AppPublicKey` (non-PV) variant is the legacy default and is actively used. [5](#0-4) 

### Recommendation

Apply the same cryptographic output check to the `AppPublicKey` variant. For the privately verifiable case, the contract can verify that `e(big_c, G2) = e(H(pk, app_id)·msk + big_y·A1, G2)` using the MPC public key and the stored `app_id`. Alternatively, require threshold-many participants to submit matching responses before resolving the yield, mirroring the off-chain threshold guarantee on-chain. At minimum, document that `AppPublicKey` requests carry no on-chain integrity guarantee and deprecate the variant in favor of `AppPublicKeyPV`. [1](#0-0) 

### Proof of Concept

1. Honest user submits `request_app_private_key` with `AppPublicKey(pk1)` (the legacy non-PV variant).
2. The contract stores the `CKDRequest` in `pending_ckd_requests` and opens a yield.
3. A single Byzantine attested participant calls `respond_ckd(request, CKDResponse { big_y: [1u8;48].into(), big_c: [2u8;48].into() })` — the exact garbage values used in the existing unit test at line 3424.
4. The contract's `match` arm for `AppPublicKey` is empty; no check runs.
5. `resolve_yields_for` resolves the yield and delivers the fabricated response to the caller.
6. The honest nodes' subsequent `respond_ckd` calls find no pending request and silently return `RequestNotFound`.
7. The app decrypts `big_c − a·big_y` and obtains attacker-controlled garbage as its "derived secret." [5](#0-4) [6](#0-5)

### Citations

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

**File:** crates/threshold-signatures/docs/confidential_key_derivation/confidential-key-derivation.md (L121-130)
```markdown

## Security Assumptions

- The *operator* is not trusted, but its TEE-enabled hardware is considered
  secure
- MPC nodes running in TEE: All are trusted and execute the protocol honestly.
  Liveness and correctness depend on this assumption, while the secrecy does
  not. Example values that should not be leaked even if a node is malicious of
  are $`s`$, $`\texttt{msk}`$ and private shares of other nodes
- The *developer* guarantee's the *app* security, and that the intended
```
