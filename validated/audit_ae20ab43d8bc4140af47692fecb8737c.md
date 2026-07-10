### Title
`respond_ckd` Skips Cryptographic Output Verification for `AppPublicKey` Variant, Allowing a Single Byzantine Participant to Resolve CKD Requests with Arbitrary Outputs - (File: `crates/contract/src/lib.rs`)

### Summary

In `respond_ckd`, the contract enforces a cryptographic pairing-equation check (`ckd_output_check`) on the submitted CKD response only when the request used the `AppPublicKeyPV` variant. When the request used the `AppPublicKey` (legacy, "privately verifiable") variant, the check arm is an empty no-op. A single malicious attested participant — strictly below the signing threshold — can call `respond_ckd` with any arbitrary `CKDResponse` (`big_c`, `big_y`) and the contract will accept it, drain the pending request queue, and deliver the garbage output to every waiting caller.

### Finding Description

In `crates/contract/src/lib.rs` at lines 675–682, `respond_ckd` branches on the request's `app_public_key` variant:

```rust
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}          // ← empty: no check
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
```

For `AppPublicKeyPV`, `ckd_output_check` (defined in `crates/contract/src/primitives/ckd.rs` lines 80–102) verifies the pairing equation `e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)`. This equation can only be satisfied by a response that was computed using the actual MPC master secret key, so even a single rogue node cannot forge a passing response.

For `AppPublicKey`, the arm is empty. No pairing check, no scalar check, no structural check of any kind is performed on `response.big_c` or `response.big_y`. The function proceeds directly to `pending_requests::resolve_yields_for`, which drains the entire fan-out queue and delivers the unverified bytes to every caller waiting on that request.

The attacker-controlled entry path is:
1. A user submits `request_app_private_key` with `app_public_key = AppPublicKey(pk1)` (the legacy variant, still accepted per the contract README).
2. A single malicious attested participant calls `respond_ckd(request, CKDResponse { big_c: <garbage>, big_y: <garbage> })`.
3. `assert_caller_is_attested_participant_and_protocol_active` passes (one attested participant suffices).
4. The `AppPublicKey` match arm is a no-op.
5. `resolve_yields_for` drains the queue and delivers the attacker-chosen bytes to all waiting callers.

The analog to `bypassSignatoryApproval` is the `AppPublicKey` variant itself: it acts as a structural bypass that causes the important cryptographic check to be skipped, exactly as the `bypassSignatoryApproval` early-return skipped the nonce and control checks in the reference report.

### Impact Explanation

The threshold guarantee for CKD is broken for the `AppPublicKey` variant. The MPC protocol is designed so that no single participant below the threshold can produce a valid CKD output — the pairing equation enforces this at the contract level for `AppPublicKeyPV`. For `AppPublicKey`, this on-chain enforcement is absent. A single Byzantine participant can:

- Unilaterally resolve any pending `AppPublicKey` CKD request with an attacker-chosen `(big_c, big_y)` pair.
- Cause every caller queued under that request (up to 128, per `MAX_PENDING_REQUEST_FAN_OUT`) to receive the garbage output.
- Permanently consume the request (it is removed from `pending_ckd_requests`); the user must resubmit and pay again.
- If the user does not independently verify the output (which requires knowing their private scalar `r`), they may use the attacker-controlled derived key material on a foreign chain, leading to loss of funds or unauthorized access.

This breaks the production safety invariant that CKD responses delivered by the contract are cryptographically bound to the MPC network's master secret key.

### Likelihood Explanation

The attacker must be an attested participant in the MPC network — a Byzantine node strictly below the signing threshold. This is explicitly within the stated attacker model ("Byzantine participant strictly below the signing threshold"). The `AppPublicKey` variant is still accepted (it is the legacy path documented in the contract README), so real users will submit requests using it. No collusion, no leaked keys, and no privileged operator access are required.

### Recommendation

Apply `ckd_output_check` unconditionally, regardless of the `app_public_key` variant. For `AppPublicKey`, the `pk2` component needed for the pairing check is absent, but the contract can require callers to upgrade to `AppPublicKeyPV` for new requests, or alternatively reject `respond_ckd` calls for `AppPublicKey` requests unless a separate on-chain verification path is defined. At minimum, the empty match arm must not silently accept arbitrary responses.

### Proof of Concept

1. User calls `request_app_private_key` with `app_public_key = AppPublicKey(g^r)` for some `r` they hold. A `CKDRequest` is stored in `pending_ckd_requests`.
2. Malicious attested participant constructs `CKDResponse { big_c: g1_identity, big_y: g1_identity }` (or any arbitrary G1 points).
3. Malicious participant calls `respond_ckd(request, fake_response)`.
4. Contract executes: `assert_caller_is_signer()` passes, `is_running_or_resharing()` passes, `accept_requests` passes, `assert_caller_is_attested_participant_and_protocol_active()` passes, `AppPublicKey` arm is empty (no check), `resolve_yields_for` drains the queue.
5. User's promise callback receives `serde_json::to_vec(&fake_response)` — attacker-controlled bytes — as the CKD output.
6. The pending request is gone; the user must resubmit. If the user uses the garbage output as a derived key, funds on the foreign chain are at risk. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/contract/src/pending_requests.rs (L66-88)
```rust
pub(crate) fn resolve_yields_for<K>(
    requests: &mut LookupMap<K, Vec<YieldIndex>>,
    request: &K,
    response_bytes: Vec<u8>,
) -> Result<(), Error>
where
    K: BorshSerialize + BorshDeserialize + Clone + Ord,
{
    let resumed = requests
        .remove(request)
        .unwrap_or_default()
        .into_iter()
        .map(|YieldIndex { data_id }| {
            env::promise_yield_resume(&data_id, response_bytes.clone());
        })
        .count();

    if resumed > 0 {
        Ok(())
    } else {
        Err(InvalidParameters::RequestNotFound.into())
    }
}
```
