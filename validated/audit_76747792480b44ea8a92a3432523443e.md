### Title
Irreversible `accept_requests = false` Freeze with No Dedicated Re-enable Path — (`File: crates/contract/src/lib.rs`)

### Summary
`MpcContract` contains an `accept_requests: bool` flag that gates every user-facing signing call (`sign`, `request_app_private_key`, `verify_foreign_transaction`) and every node response (`respond`, `respond_ckd`, `respond_verify_foreign_tx`). The flag can be set to `false` inside `verify_tee()` when TEE re-verification fails for enough participants to break the threshold relation. There is no dedicated function to set it back to `true`; the only recovery path is a subsequent successful call to `verify_tee()`. If the conditions that caused the failure cannot be resolved (e.g., all allowed image hashes have expired and attestations cannot be renewed), the contract is permanently frozen with no signing capability and no escape hatch short of a contract upgrade.

### Finding Description
`MpcContract.accept_requests` is initialized to `true` in `init()` and `init_running()`. [1](#0-0) [2](#0-1) 

The flag is checked in `check_request_preconditions()`, which is called by every user-facing request method: [3](#0-2) 

It is also checked directly in `respond()`, `respond_ckd()`, and `respond_verify_foreign_tx()`: [4](#0-3) 

The **only** place `accept_requests` is set to `false` is inside `verify_tee()`, in the branch where kicking out participants with invalid TEE attestations would violate the threshold relation: [5](#0-4) 

The **only** places `accept_requests` is set back to `true` are also inside `verify_tee()`: [6](#0-5) [7](#0-6) 

There is no standalone `enable_requests()`, `unpause()`, or emergency override function. The entire contract API surface was searched and no such function exists.

The recovery path requires:
1. Invalid participants renew their attestations via `submit_participant_info()`.
2. Someone calls `verify_tee()` again.

`submit_participant_info()` calls `attestation.verify_locally()` against the current `allowed_docker_image_hashes` whitelist: [8](#0-7) 

If all allowed image hashes have expired (the `tee_upgrade_deadline_duration` window has passed for every entry) or been removed, `submit_participant_info()` will reject every new attestation, making it impossible to restore valid TEE status for the affected participants. `verify_tee()` will then always re-enter the `accept_requests = false` branch, and the contract is permanently frozen.

### Impact Explanation
When `accept_requests = false`:
- All new `sign`, `request_app_private_key`, and `verify_foreign_transaction` calls revert with `TeeError::TeeValidationFailed`.
- All in-flight pending requests that were already enqueued cannot be resolved because `respond`, `respond_ckd`, and `respond_verify_foreign_tx` also check the flag and return an error, causing every queued yield to time out.
- The MPC network loses all signing capability. Any cross-chain assets or protocols that depend on MPC-issued signatures are permanently inaccessible — a complete freeze of funds controlled by the chain-signature contract.

This matches the Critical/Medium allowed impact: **permanent freezing of funds controlled by the MPC network** and **request-lifecycle manipulation that breaks production safety invariants**.

### Likelihood Explanation
The freeze is reachable without any privileged access:

1. During a TEE image upgrade, the `tee_upgrade_deadline_duration` window for old image hashes expires before participants have voted in and attested to the new image.
2. Any participant (or any external caller, since `verify_tee` only requires `voter_or_panic()` which checks protocol-state participation, not TEE validity) calls `verify_tee()`.
3. `reverify_and_cleanup_participants()` finds that the remaining valid participants are fewer than the threshold.
4. `accept_requests = false` is set; the contract logs "requires manual intervention."
5. Because the old image hashes have expired, `submit_participant_info()` rejects all re-attestation attempts, making recovery impossible without a contract upgrade.

This is a realistic operational scenario during any rolling TEE image upgrade and does not require collusion, physical TEE attacks, or privileged access beyond being a protocol participant.

### Recommendation
Add a dedicated, threshold-gated `vote_enable_requests()` function (analogous to the `vote_*` governance pattern already used throughout the contract) that allows participants to explicitly re-enable `accept_requests` after resolving the underlying TEE issue. Alternatively, rename the current behavior to make the permanent-freeze risk explicit in documentation and add an emergency governance escape hatch (e.g., a contract upgrade path that resets the flag). At minimum, the `verify_tee()` log message "requires manual intervention" should be backed by an actual on-chain mechanism for that intervention.

### Proof of Concept

```
State: Running, accept_requests = true
  All allowed image hashes expire (tee_upgrade_deadline_duration elapses)
  
  Participant A calls verify_tee():
    → reverify_and_cleanup_participants() returns Partial{valid=[A]}
    → validate_governance_against_reconstruction fails (1 < threshold=2)
    → accept_requests = false   ← NO RECOVERY FUNCTION EXISTS
    → returns Ok(false)

  Participant A tries submit_participant_info(new_attestation):
    → verify_locally() checks allowed_docker_image_hashes → empty/expired
    → returns Err(InvalidAttestation)   ← cannot renew attestation

  Participant A calls verify_tee() again:
    → same result: accept_requests = false

  User calls sign(...):
    → check_request_preconditions → accept_requests == false
    → panics with TeeError::TeeValidationFailed

  Node calls respond(...):
    → accept_requests == false
    → returns Err(TeeError::TeeValidationFailed)
    → all pending yields time out → all in-flight requests permanently lost

  Contract is frozen. No on-chain recovery path exists.
``` [9](#0-8) [10](#0-9) [11](#0-10)

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

**File:** crates/contract/src/lib.rs (L1693-1770)
```rust
    pub fn verify_tee(&mut self) -> Result<bool, Error> {
        log!("verify_tee: signer={}", env::signer_account_id());
        // Caller must be a participant (node or operator).
        self.voter_or_panic();
        let ProtocolContractState::Running(running_state) = &mut self.protocol_state else {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        };
        let current_params = running_state.parameters.clone();

        let tee_upgrade_deadline_duration =
            Duration::from_secs(self.config.tee_upgrade_deadline_duration_seconds);

        match self.tee_state.reverify_and_cleanup_participants(
            current_params.participants(),
            tee_upgrade_deadline_duration,
        ) {
            TeeValidationResult::Full => {
                self.accept_requests = true;
                log!("All participants have an accepted Tee status");
                Ok(true)
            }
            TeeValidationResult::Partial {
                participants_with_valid_attestation,
            } => {
                let remaining = participants_with_valid_attestation.len();
                // Defense in depth: the surviving participant set must keep the full
                // threshold relation intact — the GovernanceThreshold must still sit
                // within its bounds for the smaller set (in particular it must not
                // exceed the remaining participant count or the upper cap) and must
                // remain at least every domain's ReconstructionThreshold (the kickout
                // keeps the existing per-domain thresholds). Otherwise we refuse and
                // wait for manual intervention.
                let max_reconstruction_threshold =
                    max_reconstruction_threshold(running_state.domains.domains());
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
                }

                // here, we set it to true, because at this point, we have at least `threshold`
                // number of participants with an accepted Tee status.
                self.accept_requests = true;

                // do we want to adjust the threshold?
                //let n_participants_new = new_participants.len();
                //let new_threshold = (3 * n_participants_new + 4) / 5; // minimum 60%
                //let new_threshold = new_threshold.max(2); // but also minimum 2
                let new_threshold = usize::try_from(current_params.threshold().value())
                    .expect("threshold value fits in usize");

                let threshold_parameters = ThresholdParameters::new(
                    participants_with_valid_attestation,
                    Threshold::new(new_threshold as u64),
                )
                .expect("Require valid threshold parameters"); // this should never happen.
                current_params.validate_incoming_proposal(&threshold_parameters)?;
                // This resharing only changes the participant set, so the
                // per-domain reconstruction-threshold updates map is empty.
                let proposed_parameters =
                    ProposedThresholdParameters::new(threshold_parameters, BTreeMap::new());
                let res = running_state.transition_to_resharing_no_checks(&proposed_parameters);
                if let Some(resharing) = res {
                    self.protocol_state = ProtocolContractState::Resharing(resharing);
                }

                Ok(true)
            }
        }
    }
```

**File:** crates/contract/src/lib.rs (L1962-1962)
```rust
            accept_requests: true,
```

**File:** crates/contract/src/lib.rs (L2041-2041)
```rust
            accept_requests: true,
```

**File:** crates/contract/src/tee/tee_state.rs (L163-175)
```rust
        let accepted_measurements = self.get_accepted_measurements();
        // TODO(#3264): run DCAP in the verifier contract (Promise + callback) and
        // do the post-DCAP checks here, instead of verifying locally in-WASM.
        let AcceptedAttestation {
            attestation: verified_attestation,
            advisory_ids,
        } = attestation.verify_locally(
            expected_report_data.into(),
            Self::current_time_seconds(),
            &self.get_allowed_mpc_docker_image_hashes(tee_upgrade_deadline_duration),
            &self.get_allowed_launcher_compose_hashes(),
            &accepted_measurements,
        )?;
```
