### Title
Missing Cryptographic Validation of CKD Response for Legacy `AppPublicKey` Variant Allows Single Byzantine Participant to Forge Key Derivation Output — (File: `crates/contract/src/lib.rs`)

---

### Summary

The `respond_ckd` function enforces cryptographic output validation **only** for the `AppPublicKeyPV` (publicly verifiable) variant of a CKD request. For the legacy `AppPublicKey` variant, the response is accepted unconditionally after a single participant authentication check. A single Byzantine participant — strictly below the signing threshold — can call `respond_ckd` with an arbitrary fabricated `CKDResponse`, which is then delivered to all waiting callers without any on-chain verification, bypassing the threshold-MPC requirement entirely.

---

### Finding Description

In `respond_ckd`, after authenticating the caller as an attested participant, the function branches on the request's `app_public_key` type:

```rust
// crates/contract/src/lib.rs:675-682
match &request.app_public_key {
    dtos::CKDAppPublicKey::AppPublicKey(_) => {}   // ← NO VALIDATION
    dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
        if !ckd_output_check(&request.app_id, &response, app_pk, &public_key) {
            env::panic_str("CKD output check failed");
        }
    }
}
``` [1](#0-0) 

For `AppPublicKeyPV`, the contract calls `ckd_output_check` to verify the response against the app's public key and the MPC network's root public key — a publicly verifiable check. For the legacy `AppPublicKey` variant, the arm is a no-op: any `CKDResponse` is accepted.

The function then unconditionally calls `resolve_yields_for`, which resumes **all** pending yield-resume promises queued under that request key, delivering the (potentially fabricated) response to every waiting caller:

```rust
// crates/contract/src/lib.rs:684-688
pending_requests::resolve_yields_for(
    &mut self.pending_ckd_requests,
    &request,
    serde_json::to_vec(&response).unwrap(),
)
``` [2](#0-1) 

This is structurally inconsistent with `respond` (ECDSA/EdDSA), which **always** verifies the signature cryptographically before resolving yields:

```rust
// crates/contract/src/lib.rs:586-644
let signature_is_valid = match (&response, public_key) { ... };
if !signature_is_valid {
    return Err(RespondError::InvalidSignature.into());
}
``` [3](#0-2) 

The caller authentication (`assert_caller_is_attested_participant_and_protocol_active`) only requires the caller to be **one** attested participant in the active set — not a threshold of them:

```rust
// crates/contract/src/lib.rs:2389-2403
fn assert_caller_is_attested_participant_and_protocol_active(&self) {
    let participants = self.protocol_state.active_participants();
    Self::assert_caller_is_signer();
    let attestation_check = self
        .tee_state
        .is_caller_an_attested_participant(participants);
    assert_matches::assert_matches!(attestation_check, Ok(()), "Caller must be an attested participant");
}
``` [4](#0-3) 

The `respond_ckd` function is part of the public Node API and is callable by any single attested participant directly on-chain: [5](#0-4) 

---

### Impact Explanation

A single Byzantine participant (below the signing threshold) can:

1. Observe a pending `request_app_private_key` call using the legacy `AppPublicKey` format.
2. Construct a `CKDResponse` containing key material they control (e.g., `big_y` and `big_c` corresponding to a key they know).
3. Call `respond_ckd` directly on-chain with the fabricated response.
4. The contract accepts it (no validation for `AppPublicKey`), and `resolve_yields_for` delivers the forged output to all waiting callers.

The user receives a confidential key derivation output that was **not** computed by the threshold MPC protocol. If the attacker chose the fabricated key, they can decrypt any data the user subsequently encrypts with it. This is a complete bypass of the threshold-signature requirement for CKD — one participant forges what should require `t`-of-`n` cooperation.

**Impact class:** Critical — Bypass of threshold-signature requirements; unauthorized confidential key derivation output without required participant authorization.

---

### Likelihood Explanation

- The `AppPublicKey` (legacy) format is still documented and accepted by the contract.
- Any single participant in the MPC network can exploit this; no collusion is required.
- The attacker only needs to be an attested participant (a role held by every active MPC node operator).
- The attack is a direct on-chain call with no off-chain coordination needed.
- The victim has no way to detect the forgery, since the legacy format is not publicly verifiable by design.

---

### Recommendation

1. **For `AppPublicKey` (legacy):** Add an on-chain cryptographic check analogous to `ckd_output_check`. If the legacy format structurally cannot be verified on-chain (because verification requires the app's secret key), this must be documented as a known limitation and the format should be deprecated in favor of `AppPublicKeyPV`.
2. **Deprecate `AppPublicKey`:** Require all new CKD requests to use `AppPublicKeyPV`, which supports on-chain verification. Reject `AppPublicKey` requests in `respond_ckd` until a validation path exists.
3. **Consistency:** Align `respond_ckd` with `respond` — never call `resolve_yields_for` unless the response has been cryptographically validated.

---

### Proof of Concept

```
1. Alice calls request_app_private_key({
       app_public_key: AppPublicKey(alice_g1_pk),
       derivation_path: "my-app",
       domain_id: 0
   }) with 1 yoctoNEAR deposit.
   → Contract stores pending CKD request, Alice's NEAR call is suspended (yield).

2. Malicious participant Eve (a single attested participant, below threshold)
   constructs a CKDRequest matching Alice's (same app_id, domain_id, app_public_key).

3. Eve calls respond_ckd(request, CKDResponse {
       big_y: eve_controlled_g1_point,   // key Eve knows
       big_c: eve_controlled_ciphertext,
   }).

4. Contract checks:
   - assert_caller_is_signer()                          → passes (Eve calls directly)
   - is_running_or_resharing()                          → passes
   - accept_requests                                    → passes
   - assert_caller_is_attested_participant_and_protocol_active() → passes (Eve is one participant)
   - match app_public_key { AppPublicKey(_) => {} }     → NO VALIDATION, passes
   - resolve_yields_for(...)                            → delivers Eve's fabricated response to Alice

5. Alice's suspended call resumes with Eve's forged CKDResponse.
   Alice uses the derived key (which Eve knows) to encrypt sensitive data.
   Eve can decrypt it.
```

### Citations

**File:** crates/contract/src/lib.rs (L642-644)
```rust
        if !signature_is_valid {
            return Err(RespondError::InvalidSignature.into());
        }
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

**File:** crates/contract/src/lib.rs (L2389-2403)
```rust
    fn assert_caller_is_attested_participant_and_protocol_active(&self) {
        let participants = self.protocol_state.active_participants();

        Self::assert_caller_is_signer();

        let attestation_check = self
            .tee_state
            .is_caller_an_attested_participant(participants);

        assert_matches::assert_matches!(
            attestation_check,
            Ok(()),
            "Caller must be an attested participant"
        );
    }
```
