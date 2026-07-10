### Title
`accept_requests` Flag Not Reset After Resharing Completion Leaves Contract in Broken State - (File: `crates/contract/src/lib.rs`)

### Summary

`verify_tee` sets `accept_requests = false` when TEE validation fails below threshold. When a subsequent resharing is completed via `vote_reshared`, the flag is never reset to `true`. All future signature requests (`sign`, `respond`, `respond_ckd`, `respond_verify_foreign_tx`, `request_app_private_key`) permanently fail with `TeeValidationFailed`, even though the resharing was performed precisely to restore a healthy participant set.

### Finding Description

`verify_tee` (callable by any participant) evaluates the TEE status of all current participants. When the `TeeValidationResult::Partial` branch fires and the surviving participant count would break the threshold relation, the contract sets `accept_requests = false` and returns without triggering a resharing:

```rust
// lib.rs:1727-1738
if let Err(err) = ThresholdParameters::validate_governance_against_reconstruction(...) {
    log!("...This requires manual intervention...");
    self.accept_requests = false;   // ← flag cleared
    return Ok(false);
}
```

The intended recovery path is for nodes to upgrade their TEE attestations and then for participants to call `vote_new_parameters` to trigger a manual resharing. `vote_new_parameters` does not check `accept_requests` and does not set it. When resharing concludes via `vote_reshared`, the function transitions the protocol state and spawns six cleanup promises, but never touches `accept_requests`:

```rust
// lib.rs:1170-1236
if let Some(new_state) = self.protocol_state.vote_reshared(key_event_id)? {
    self.protocol_state = new_state;          // ← state updated
    self.recompute_available_foreign_chains();
    // six detached cleanup promises...
    // accept_requests is NEVER reset here
}
```

After resharing, every user-facing and node-facing request path checks the flag:

- `check_request_preconditions` (line 300): `sign`, `request_app_private_key`, `verify_foreign_transaction`
- `respond` (line 579): node signature submission
- `respond_ckd` (line 662): node CKD submission
- `respond_verify_foreign_tx` (line 711): node foreign-tx submission

All of these return `TeeError::TeeValidationFailed` while `accept_requests == false`, permanently blocking the MPC network's core function.

### Impact Explanation

After a resharing that was triggered to resolve a TEE degradation, the contract remains in a state where no signature can be requested or fulfilled. Pending yield-resume promises for in-flight `sign` calls time out and return errors to callers. The MPC network is effectively halted for all users until a participant separately calls `verify_tee` again — a step that is not documented as mandatory post-resharing and is not enforced by the protocol. This breaks the request-lifecycle and contract execution-flow invariants of the MPC system.

**Impact: Medium** — contract execution-flow manipulation that breaks production safety invariants without requiring network-level DoS or operator misconfiguration.

### Likelihood Explanation

The sequence requires:
1. `verify_tee` to be called and return `false` (TEE degradation below threshold — a realistic operational event as attestations expire).
2. Nodes to upgrade their TEE attestations.
3. Participants to use `vote_new_parameters` (the standard governance path) rather than calling `verify_tee` again to recover.

Step 3 is the key: operators following the documented governance flow for participant-set changes will naturally use `vote_new_parameters`, not `verify_tee`. The bug is latent in the normal operational path.

**Likelihood: Low** — requires a specific ordering of events, but the ordering is a natural consequence of the documented recovery procedure.

### Recommendation

In `vote_reshared`, after a successful transition to `Running` state, reset `accept_requests = true`:

```rust
if let Some(new_state) = self.protocol_state.vote_reshared(key_event_id)? {
    self.protocol_state = new_state;
    self.accept_requests = true;   // ← add this
    self.recompute_available_foreign_chains();
    // ... cleanup promises ...
}
```

Alternatively, spawn a `verify_tee` call as part of the post-resharing promise chain so the flag is re-evaluated against the new participant set automatically.

### Proof of Concept

1. Deploy contract with 5 participants, GovernanceThreshold = 5, reconstruction threshold = 5.
2. Expire one participant's TEE attestation so only 4 remain valid.
3. Call `verify_tee` as participant[0]. The `Partial` branch fires; dropping to 4 would break the threshold relation (GovernanceThreshold 5 > participant count 4). `accept_requests` is set to `false`. Contract stays `Running`.
4. The expired participant renews their attestation. Now all 5 have valid TEE.
5. All 5 participants call `vote_new_parameters` with the same 5-participant set (standard governance rotation). Resharing is triggered.
6. All 5 candidates call `start_reshare_instance` then `vote_reshared`. Resharing completes; `protocol_state` transitions to `Running`.
7. Call `sign(...)` as any user. The call reaches `check_request_preconditions` → `if !self.accept_requests { env::panic_str(&TeeError::TeeValidationFailed.to_string()) }` and panics.
8. Call `respond(...)` as any node. Hits `if !self.accept_requests { return Err(TeeError::TeeValidationFailed.into()); }` and returns error.

The contract is now permanently unable to process signatures until `verify_tee` is called again — a step not enforced or documented as part of the resharing flow. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L298-302)
```rust
        // 4. Refuse the request if the contract is not currently accepting requests
        //    (e.g. because TEE validation has failed).
        if !self.accept_requests {
            env::panic_str(&TeeError::TeeValidationFailed.to_string())
        }
```

**File:** crates/contract/src/lib.rs (L579-581)
```rust
        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }
```

**File:** crates/contract/src/lib.rs (L662-664)
```rust
        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }
```

**File:** crates/contract/src/lib.rs (L711-713)
```rust
        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }
```

**File:** crates/contract/src/lib.rs (L1170-1173)
```rust
        if let Some(new_state) = self.protocol_state.vote_reshared(key_event_id)? {
            // Resharing has concluded, transition to running state
            self.protocol_state = new_state;
            self.recompute_available_foreign_chains();
```

**File:** crates/contract/src/lib.rs (L1727-1738)
```rust
                if let Err(err) = ThresholdParameters::validate_governance_against_reconstruction(
                    u64::try_from(remaining).expect("participant count fits in u64"),
                    current_params.threshold(),
                    max_reconstruction_threshold,
                ) {
                    log!(
                        "Kicking out participants with an invalid TEE status would break the threshold relation ({:?}); {} participants remain with a valid TEE status. This requires manual intervention. We will not accept new signature requests as a safety precaution.",
                        err,
                        remaining,
                    );
                    self.accept_requests = false;
                    return Ok(false);
```
