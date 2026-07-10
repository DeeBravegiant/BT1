### Title
Missing CKD Response Validation for `AppPublicKey` Variant Allows Byzantine Participant to Deliver Fabricated Confidential Key Derivation Output - (File: crates/contract/src/lib.rs)

### Summary
`respond_ckd` validates the CKD response for `AppPublicKeyPV` requests via a BLS12-381 pairing check, but has a completely empty (no-op) arm for `AppPublicKey` requests. A single Byzantine participant below the signing threshold can submit an arbitrary fabricated `CKDResponse` for any `AppPublicKey` request, and the contract will accept and deliver it to the user without any cryptographic validation, bypassing the threshold authorization requirement for confidential key derivation.

### Finding Description
In `respond_ckd`, the match on `request.app_public_key` has two arms:

```rust
// crates/contract/src/lib.rs lines 675-682
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← EMPTY: no validation
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

For `AppPublicKeyPV`, the contract enforces `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)` via `ckd_output_check`. For `AppPublicKey`, the arm is empty — any `CKDResponse` with arbitrary `big_y` and `big_c` values passes unconditionally. The contract then calls `resolve_yields_for` to deliver this fabricated response to the waiting user.

The `AppPublicKey` variant is the legacy "privately verifiable" mode and remains fully supported in production. The `request_app_private_key` entry point accepts both variants without restriction.

The root cause is structurally identical to the reported BAMM `receive` issue: a branch that should enforce a condition (response validity) instead has an empty body, silently accepting inputs that should be rejected or at minimum validated.

### Impact Explanation
A Byzantine participant can call `respond_ckd` with a fabricated `CKDResponse` (arbitrary `big_y`, `big_c`) for any pending `AppPublicKey` CKD request. The contract accepts it without any cryptographic check and resolves all pending yields for that request with the fabricated output. The user receives a derived key that was not produced by the threshold MPC protocol — a confidential key derivation output without the required threshold participant authorization. If the user subsequently uses this fabricated key to control funds on a foreign chain, they lose access to those funds. The attacker who controls the fabricated output values may be able to predict or control the key the user derives, depending on the `unmask` computation.

### Likelihood Explanation
Exploitation requires a single Byzantine participant who is an attested member of the network — below the signing threshold. The attacker races to submit the fabricated `respond_ckd` call before honest nodes submit the correct response. Since `resolve_yields_for` resolves on the first accepted response, the attacker only needs to win the race once per targeted request. No collusion above threshold is required.

### Recommendation
- Require callers to use `AppPublicKeyPV` for all new CKD requests, deprecating `AppPublicKey` for production use, since only `AppPublicKeyPV` allows on-chain output verification.
- If `AppPublicKey` must remain supported, document explicitly that the contract cannot validate the response for this variant and that users must verify the output off-chain before using the derived key.
- At minimum, add basic point-validity checks (e.g., reject identity points for `big_y` and `big_c`) to raise the bar for trivial fabrication.

### Proof of Concept
1. User calls `request_app_private_key` with `CKDAppPublicKey::AppPublicKey(app_pk)` where `app_pk = a·G1`.
2. Byzantine participant calls `respond_ckd` with `request` matching the pending entry and `response = CKDResponse { big_y: k·G1, big_c: identity }` for attacker-chosen `k`.
3. The match arm at line 676 is empty — `ckd_output_check` is never called.
4. `resolve_yields_for` at line 684 resolves all pending yields for this request with the fabricated response.
5. The user's `return_ck_and_clean_state_on_success` callback fires with the attacker-controlled `big_y`/`big_c`, delivering a derived key not produced by the threshold protocol. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L469-512)
```rust
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
