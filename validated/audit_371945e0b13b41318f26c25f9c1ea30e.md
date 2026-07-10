### Title
Unverified CKD Response Accepted for `AppPublicKey` Variant Allows Single Byzantine Participant to Forge Confidential Key Derivation Output — (File: `crates/contract/src/lib.rs`)

---

### Summary

`respond_ckd` performs no cryptographic verification of the `CKDResponse` when the pending request used the `AppPublicKey` (legacy, single G1 point) variant. A single malicious attested participant — strictly below the signing threshold — can call `respond_ckd` with an arbitrary forged `{big_y, big_c}` response, which the contract accepts unconditionally and delivers to the original caller. This bypasses the threshold-many-participant authorization requirement that is the entire security basis of the CKD protocol.

---

### Finding Description

In `respond_ckd`, the contract branches on the `app_public_key` variant stored in the `CKDRequest`:

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

For `AppPublicKeyPV`, the contract calls `ckd_output_check`, which verifies the BLS pairing relationship between `app_id`, `response`, `app_pk`, and the network's root public key. For `AppPublicKey`, the branch is an empty no-op — the contract performs zero verification of `big_y` or `big_c` before calling `resolve_yields_for` and delivering the response to the waiting caller. [2](#0-1) 

The `CKDRequest` (including `app_id`, `domain_id`, and `app_public_key`) is stored in the public contract state and is readable by any participant via `get_pending_ckd_request`. [3](#0-2) 

The `AppPublicKey` variant is documented as "privately verifiable" — the caller is expected to verify the response off-chain using their `app_secret_key`. However, the contract provides no enforcement of this, no warning to the caller, and no indication that the response was not produced by threshold-many honest participants. [4](#0-3) 

---

### Impact Explanation

A single Byzantine attested participant can:

1. Observe a pending `CKDRequest` with `AppPublicKey` variant in contract state.
2. Construct an arbitrary `CKDResponse { big_y, big_c }` — for example, one where `big_c` encodes a key the attacker controls.
3. Call `respond_ckd(request, forged_response)` before honest nodes respond.
4. The contract accepts the forged response unconditionally and resumes the caller's yield with the attacker-chosen ciphertext.

The caller receives a derived key that was not produced by the MPC threshold protocol. If the caller (a NEAR smart contract or user) uses this key to control funds — which is the primary use case for CKD — those funds are either inaccessible (garbage key) or accessible to the attacker (if the attacker chose `big_c` to encode a key they know). This constitutes **confidential key derivation output without the required participant authorization**, matching the Critical impact tier.

---

### Likelihood Explanation

- The `AppPublicKey` variant is the legacy/default mode and is actively used (the `AppPublicKeyPV` variant is newer and opt-in).
- Any single attested participant can execute this attack; no collusion is required.
- The pending request map is public; the attacker does not need any privileged information beyond their participant status.
- The attack window is the time between the request being indexed by nodes and the first honest `respond_ckd` call — typically several seconds to minutes.
- The attacker's forged call is indistinguishable from a legitimate response at the contract level.

---

### Recommendation

Apply the same `ckd_output_check` verification to the `AppPublicKey` variant that is already applied to `AppPublicKeyPV`. If the `AppPublicKey` variant cannot support on-chain verification by design, the contract should at minimum require that the response be co-signed or attested by threshold-many participants before being accepted, or deprecate the unverified variant entirely in favor of `AppPublicKeyPV`.

---

### Proof of Concept

```rust
// Attacker is a single attested participant (below signing threshold).
// Step 1: Read the pending CKD request from contract state.
let pending: CKDRequest = contract.get_pending_ckd_request(&known_request).unwrap();

// Step 2: Craft a forged response with attacker-chosen values.
let forged_response = CKDResponse {
    big_y: dtos::Bls12381G1PublicKey([0xAA; 48]), // arbitrary G1 point
    big_c: dtos::Bls12381G1PublicKey([0xBB; 48]), // attacker-chosen ciphertext
};

// Step 3: Call respond_ckd — contract performs no verification for AppPublicKey variant.
// assert_caller_is_attested_participant_and_protocol_active() passes (attacker is attested).
contract.respond_ckd(pending, forged_response).unwrap();
// → resolve_yields_for delivers forged_response to the original caller.
// → Caller receives a derived key not produced by the MPC threshold protocol.
```

The existing unit test `respond_ckd__should_succeed_when_response_is_valid_and_request_exists` confirms this path: it passes `CKDResponse { big_y: [1u8;48], big_c: [2u8;48] }` — clearly not a cryptographically valid CKD output — and the contract accepts it without error for the `AppPublicKey` variant. [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L462-512)
```rust
    /// To avoid overloading the network with too many requests,
    /// we ask for a small deposit for each ckd request.
    ///
    /// Note: identity points are accepted in `AppPublicKeyPV` to support use cases
    /// where the derived key is intentionally public (no encryption).
    #[handle_result]
    #[payable]
    pub fn request_app_private_key(&mut self, request: CKDRequestArgs) {
        log!(
            "request_app_private_key: predecessor={:?}, request={:?}",
            env::predecessor_account_id(),
            request
        );

        let domain_id: DomainId = request.domain_id;
        let (_, predecessor) = self.check_request_preconditions(
            domain_id,
            DomainPurpose::CKD,
            Gas::from_tgas(self.config.ckd_call_gas_attachment_requirement_tera_gas),
            MINIMUM_CKD_REQUEST_DEPOSIT,
        );

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
