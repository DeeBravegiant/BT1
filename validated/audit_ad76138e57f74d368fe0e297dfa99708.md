### Title
`respond_ckd` Authenticates Against Wrong Participant Set During Resharing, Enabling Fabricated CKD Response Injection for `AppPublicKey` Variant - (File: `crates/contract/src/lib.rs`)

### Summary

During the `Resharing` protocol state, `respond_ckd` authenticates the caller against the **new/proposed** participant set instead of the **old/previous** participant set. Because CKD signing during resharing uses the old key shares (held only by old participants), a newly-added participant who does not hold any old key share can call `respond_ckd` and inject an arbitrary fabricated `CKDResponse`. For the `AppPublicKey` (privately-verifiable) variant, the contract performs **no cryptographic verification** of the response, so the fabricated values are accepted and delivered to the user as their confidential key.

### Finding Description

`assert_caller_is_attested_participant_and_protocol_active` resolves the participant set by calling `active_participants()`:

```rust
// crates/contract/src/lib.rs:2389-2402
fn assert_caller_is_attested_participant_and_protocol_active(&self) {
    let participants = self.protocol_state.active_participants();
    ...
    let attestation_check = self.tee_state.is_caller_an_attested_participant(participants);
    ...
}
``` [1](#0-0) 

`active_participants()` during `Resharing` returns the **new** (proposed) participants, not the old ones:

```rust
// crates/contract/src/state.rs:255-270
ProtocolContractState::Resharing(state) => {
    state.resharing_key.proposed_parameters().participants()  // NEW participants
}
``` [2](#0-1) 

`respond_ckd` calls this guard and then performs **no cryptographic check** for the `AppPublicKey` variant:

```rust
// crates/contract/src/lib.rs:653-689
pub fn respond_ckd(&mut self, request: CKDRequest, response: CKDResponse) -> Result<(), Error> {
    ...
    self.assert_caller_is_attested_participant_and_protocol_active(); // checks NEW participants
    ...
    match &request.app_public_key {
        dtos::CKDAppPublicKey::AppPublicKey(_) => {}  // ← NO VERIFICATION
        dtos::CKDAppPublicKey::AppPublicKeyPV(app_pk) => {
            if !ckd_output_check(...) { env::panic_str("CKD output check failed"); }
        }
    }
    pending_requests::resolve_yields_for(&mut self.pending_ckd_requests, &request, ...)
}
``` [3](#0-2) 

The `AppPublicKeyPV` variant is protected by `ckd_output_check` (a BLS12-381 pairing equation):

```rust
// crates/contract/src/primitives/ckd.rs:80-101
pub(crate) fn ckd_output_check(...) -> bool {
    // verifies e(big_c, g2) = e(big_y, app_pk2) · e(hash_point, public_key)
}
``` [4](#0-3) 

The `AppPublicKey` variant has no equivalent check. The design intent is that the user's private key `a` is needed to decrypt the result, but the contract never enforces that the submitted `(big_y, big_c)` is a valid protocol output.

The `respond` (signature) and `respond_verify_foreign_tx` functions share the same wrong-participant-set issue during resharing, but both perform cryptographic signature verification that prevents a new participant from forging a valid response without the old key shares. `respond_ckd` with `AppPublicKey` has no such backstop.

### Impact Explanation

**Critical — Unauthorized confidential key derivation output without required participant authorization.**

An attacker who is a newly-added participant (in the new set, not the old set) with a valid TEE attestation can:

1. Observe a pending `CKDRequest` with `AppPublicKey` variant (the request is stored in public contract state).
2. Choose an arbitrary scalar `r`.
3. Compute `big_y = r·G1` and `big_c = r·pk` where `pk` is the user's public `AppPublicKey` (also public).
4. Call `respond_ckd` before the legitimate old participants do, injecting `(big_y, big_c)`.
5. The contract accepts the response (new participant passes the resharing-state participant check; no output verification for `AppPublicKey`).
6. The user derives their "confidential key" as `big_c / a = r·G1`, which the attacker knows because they chose `r`.

The attacker therefore learns the user's derived confidential key — a complete break of the CKD confidentiality guarantee. Additionally, the legitimate response from old participants arrives after the request is already resolved and is silently discarded.

### Likelihood Explanation

- Resharing is a routine operational event (participant rotation, TEE upgrades via `verify_tee`).
- The `AppPublicKey` (privately-verifiable, no `--publicly-verifiable` flag) is the default/legacy variant used by most callers.
- Pending `CKDRequest` keys are readable from public contract state, so the attacker needs no privileged information beyond their own TEE attestation.
- The attacker only needs to be a newly-added participant with a valid attestation — a role that is explicitly granted during resharing.
- The attack is a simple front-run of the legitimate `respond_ckd` call; no threshold collusion is required.

### Recommendation

In `respond_ckd`, authenticate the caller against the **old** participant set during resharing (i.e., `previous_running_state.parameters.participants()`), not the new one. Concretely, introduce a resharing-aware variant of `assert_caller_is_attested_participant_and_protocol_active` that selects the previous participants when the state is `Resharing`:

```rust
// During Resharing, signing/CKD uses old keys → authenticate against old participants
ProtocolContractState::Resharing(state) => {
    state.previous_running_state.parameters.participants()
}
```

Additionally, consider adding cryptographic output verification for the `AppPublicKey` variant analogous to `ckd_output_check` for `AppPublicKeyPV`, so that even a legitimately-authenticated participant cannot inject a fabricated response.

### Proof of Concept

```
State: Resharing (old participants: {A, B, C}, new participants: {A, B, C, D})
Attacker: D (new participant, valid TEE attestation, not in old set)

1. User calls request_app_private_key(AppPublicKey(pk), domain_id=X)
   → CKDRequest stored in pending_ckd_requests

2. D observes the pending CKDRequest on-chain (public state).

3. D chooses scalar r, computes big_y = r·G1, big_c = r·pk.

4. D calls respond_ckd(request, CKDResponse{big_y, big_c}):
   - assert_caller_is_attested_participant_and_protocol_active():
       active_participants() → new participants {A,B,C,D}  ← D passes ✓
   - AppPublicKey branch: no verification ← passes ✓
   - resolve_yields_for: request resolved, user receives (big_y, big_c)

5. User computes key = big_c · (1/a) = r·pk·(1/a) = r·G1.
   D knows r, so D knows the user's derived key r·G1.

6. Legitimate respond_ckd from old participants A/B/C arrives → request already
   resolved, silently ignored.
``` [5](#0-4) [6](#0-5) [1](#0-0)

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

**File:** crates/contract/src/lib.rs (L2389-2402)
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
```

**File:** crates/contract/src/state.rs (L255-270)
```rust
    pub fn active_participants(&self) -> &Participants {
        match self {
            ProtocolContractState::Initializing(state) => {
                state.generating_key.proposed_parameters().participants()
            }
            ProtocolContractState::Running(state) => state.parameters.participants(),
            ProtocolContractState::Resharing(state) => {
                state.resharing_key.proposed_parameters().participants()
            }
            ProtocolContractState::NotInitialized => {
                panic!(
                    "Protocol must be Initializing, Running, or Resharing to access active participants"
                );
            }
        }
    }
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
