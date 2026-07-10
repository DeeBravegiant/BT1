### Title
Single-Vote `vote_abort_key_event_instance` Enables Indefinite Resharing/Keygen DoS by Any Proposed Participant - (File: crates/contract/src/state/key_event.rs)

---

### Summary

A single Byzantine participant in the proposed participant set can call `vote_abort_key_event_instance` to immediately abort any active key resharing or keygen attempt. Because one vote is sufficient to abort (no threshold required), this can be repeated on every new attempt the leader starts, permanently preventing resharing or keygen from completing. This is the structural analog to the Linea fallback-operator renunciation: a low-privilege actor resets a critical state on each cycle, blocking legitimate protocol progress indefinitely.

---

### Finding Description

`vote_abort_key_event_instance` in `lib.rs` is gated by `assert_caller_is_attested_participant_and_protocol_active`, which for the Resharing state checks membership in the **new proposed** participant set. [1](#0-0) 

It delegates to `KeyEvent::vote_abort` in `key_event.rs`: [2](#0-1) 

A single call sets `self.instance = None`, immediately aborting the active attempt. The only guard is `VoteAlreadySubmitted`, which fires only if the caller already cast a **success** vote (`completed.contains(&candidate)`). A participant who has not yet voted success can abort unconditionally. [3](#0-2) 

This is confirmed by the unit test comment: *"check that vote_abort immediately causes failure"*: [4](#0-3) 

After an abort, the leader (lowest-ID participant) must call `start_reshare_instance` / `start_keygen_instance` to begin a new attempt with the next `attempt_id`. The Byzantine participant can immediately abort that attempt too, since each new attempt has a fresh `attempt_id` and the `completed` set is reset. [5](#0-4) 

The `vote_cancel_resharing` escape hatch requires **threshold** votes from the *previous* running-state participants, which is a significant operational burden and does not prevent the Byzantine participant from aborting the next resharing proposal if they remain in it. [6](#0-5) 

---

### Impact Explanation

A Byzantine participant in the proposed set can prevent any resharing or keygen from ever completing:

- **Participant-set changes are permanently blocked**: operators cannot add, remove, or rotate participants.
- **TEE-triggered kickouts are nullified**: when `verify_tee` detects an expired attestation and transitions to Resharing, a Byzantine participant in the new proposed set can abort every attempt, keeping the contract stuck in Resharing state indefinitely.
- **Domain keygen is blocked**: the same single-vote abort applies to `InitializingContractState`, preventing new signing domains from being activated. [7](#0-6) 

The contract remains in Resharing/Initializing state; signing continues (the `respond` path checks `is_running_or_resharing()`), but the participant set and key set cannot be updated, breaking the safety invariant that the network can always recover from a compromised or expired participant.

---

### Likelihood Explanation

- Requires only **one** Byzantine participant in the proposed set — no threshold collusion.
- The attacker must hold a valid TEE attestation and be included in the proposal, which is a realistic condition for any current participant who turns adversarial or for a newly proposed participant who is malicious.
- The attack costs only repeated NEAR transactions (cheap) and requires no special timing or off-chain capability.
- Operators can mitigate by cancelling resharing (threshold votes) and re-proposing without the Byzantine participant, but this is a significant operational burden and the Byzantine participant can abort the new proposal too if they are re-included.

---

### Recommendation

Require a **threshold** of abort votes before aborting a key event instance, mirroring the threshold requirement in `vote_cancel_resharing`. Alternatively, track per-participant abort votes and only abort when the count reaches threshold:

```rust
// Instead of immediately setting self.instance = None,
// collect abort votes and abort only when threshold is reached.
self.abort_votes.insert(candidate);
if self.abort_votes.len() >= self.parameters.threshold().value() as usize {
    self.instance = None;
    self.abort_votes.clear();
}
```

This ensures a single Byzantine participant cannot unilaterally abort an attempt, while still allowing the network to recover from a genuinely failed attempt when enough participants agree.

---

### Proof of Concept

1. Network is Running with participants `[A, B, C]`, threshold 2.
2. Operators vote to replace C with D: proposed set `[A, B, D]`, threshold 2. Contract enters Resharing.
3. D is Byzantine and wants to prevent the resharing from completing.
4. Leader A calls `start_reshare_instance(key_event_id_0)`.
5. D calls `vote_abort_key_event_instance(key_event_id_0)` → `instance = None` immediately.
6. Leader A calls `start_reshare_instance(key_event_id_1)` (next `attempt_id`).
7. D calls `vote_abort_key_event_instance(key_event_id_1)` → `instance = None` immediately.
8. Steps 6–7 repeat indefinitely. Resharing never completes; C is never replaced; the participant set is permanently frozen. [2](#0-1) [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L1282-1295)
```rust
    /// Casts a vote to abort the current key event instance. If succesful, the contract aborts the
    /// instance and a new instance with the next attempt_id can be started.
    #[handle_result]
    pub fn vote_abort_key_event_instance(&mut self, key_event_id: KeyEventId) -> Result<(), Error> {
        log!(
            "vote_abort_key_event_instance: signer={}",
            env::signer_account_id()
        );

        self.assert_caller_is_attested_participant_and_protocol_active();

        self.protocol_state
            .vote_abort_key_event_instance(key_event_id)
    }
```

**File:** crates/contract/src/lib.rs (L1687-1770)
```rust
    /// Verifies if all current participants have an accepted TEE state.
    /// Automatically enters a resharing, in case one or more participants do not have an accepted
    /// TEE state.
    /// Returns `false` and stops the contract from accepting new signature requests or responses,
    /// in case less than `threshold` participants run in an accepted TEE State.
    #[handle_result]
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

**File:** crates/contract/src/state/key_event.rs (L143-158)
```rust
    /// Casts a vote to abort the current keygen instance.
    /// A new instance needs to be started later to start a new keygen attempt.
    pub fn vote_abort(&mut self, key_event_id: KeyEventId) -> Result<(), Error> {
        let candidate = self.verify_vote(&key_event_id)?;
        if self
            .instance
            .as_ref()
            .unwrap()
            .completed
            .contains(&candidate)
        {
            return Err(VoteError::VoteAlreadySubmitted.into());
        }
        self.instance = None;
        Ok(())
    }
```

**File:** crates/contract/src/state/initializing.rs (L278-284)
```rust
            // check that vote_abort immediately causes failure.
            env.set_signer(&leader.0);
            state.start(key_event.next_attempt(), 0).unwrap();
            let key_event = state.generating_key.current_key_event_id().unwrap();
            env.set_signer(candidates.iter().next().unwrap());
            state.vote_abort(key_event).unwrap();
            assert!(!state.generating_key.is_active());
```

**File:** crates/contract/src/state/resharing.rs (L97-106)
```rust
    /// Starts a new attempt to reshare the key for the current domain.
    /// Returns an Error if the signer is not the leader (the participant with the lowest ID).
    pub fn start(
        &mut self,
        key_event_id: KeyEventId,
        key_event_timeout_blocks: u64,
    ) -> Result<(), Error> {
        self.resharing_key
            .start(key_event_id, key_event_timeout_blocks)
    }
```

**File:** crates/contract/src/state/resharing.rs (L173-195)
```rust
    pub fn vote_cancel_resharing(&mut self) -> Result<Option<RunningContractState>, Error> {
        let previous_running_participants = self.previous_running_state.parameters.participants();
        let authenticated_candidate = AuthenticatedAccountId::new(previous_running_participants)?;
        self.cancellation_requests.insert(authenticated_candidate);

        let cancellation_votes_count = self.cancellation_requests.len() as u64;
        let previous_running_threshold = self.previous_running_state.parameters.threshold();

        let threshold_cancellation_votes_reached: bool =
            cancellation_votes_count >= previous_running_threshold.value();

        let running_state = if threshold_cancellation_votes_reached {
            let mut previous_running_state = self.previous_running_state.clone();
            let prospective_epoch_id = self.prospective_epoch_id();
            previous_running_state.previously_cancelled_resharing_epoch_id =
                Some(prospective_epoch_id);

            Some(previous_running_state)
        } else {
            None
        };

        Ok(running_state)
```
