### Title
Expired TEE Attestations Still Count Toward Governance Vote Thresholds - (File: crates/contract/src/lib.rs)

### Summary
Governance voting functions (`vote_code_hash`, `vote_add_launcher_hash`, `vote_add_os_measurement`, `vote_remove_launcher_hash`, `vote_remove_os_measurement`) use `voter_or_panic()` — a participant-membership check — rather than `assert_caller_is_attested_participant_and_protocol_active`, which additionally verifies that the caller's TEE attestation is currently valid. As a result, a participant whose TEE attestation has expired (i.e., whose execution environment is no longer verified) can still cast new votes and have previously cast votes counted toward governance thresholds that control which TEE images are trusted by the network.

### Finding Description
The contract enforces two distinct caller-authorization guards:

1. `assert_caller_is_attested_participant_and_protocol_active` — used in `vote_pk` and `vote_reshared` — verifies both participant membership **and** valid TEE attestation status.
2. `voter_or_panic()` — used in all governance voting functions — verifies only participant membership, without checking whether the caller's TEE attestation is still valid. [1](#0-0) [2](#0-1) 

The vote-counting logic in `MeasurementVotes::count_votes`, `LauncherHashVotes::count_votes`, and `CodeHashesVotes` simply tallies all stored votes without filtering by attestation validity: [3](#0-2) [4](#0-3) 

The post-resharing cleanup (`clean_non_participant_votes`) removes votes only from accounts that are no longer participants — it does **not** remove or invalidate votes from participants whose attestations have expired but who remain in the participant set: [5](#0-4) 

The `verify_tee` function can eventually trigger resharing to remove participants with expired attestations, but until `verify_tee` is explicitly called and resharing completes, the expired-attestation participant remains in the active set and their votes count: [6](#0-5) 

### Impact Explanation
The governance actions controlled by these votes determine which MPC docker image hashes, launcher image hashes, and OS measurements are trusted by the network. A participant whose TEE attestation has expired — meaning their execution environment is no longer cryptographically verified — can:

- Cast new votes for adding a malicious MPC image hash to the allowed list (`vote_code_hash`), which would permit unauthorized nodes running that image to submit attestations and eventually join the signing network.
- Have previously cast votes persist and count toward the threshold even after their attestation expires, without any invalidation.
- For `vote_remove_launcher_hash` and `vote_remove_os_measurement` (which require **all** participants to vote), an expired-attestation participant's vote counts toward the unanimity requirement, potentially allowing removal of a security-critical hash with fewer genuinely-trusted participants agreeing.

This breaks the core safety invariant: only participants with currently valid, verified TEE environments should influence which TEE images are trusted. The impact maps to **Medium** — participant-state and contract execution-flow manipulation that breaks production safety/accounting invariants. [7](#0-6) [8](#0-7) 

### Likelihood Explanation
TEE attestations have explicit expiry timestamps. The `verify_tee` function is not called automatically — it must be triggered by an operator or participant. Between attestation expiry and the completion of the resharing triggered by `verify_tee`, the window during which an expired-attestation participant can cast or retain counted votes is real and non-trivial. A single Byzantine participant below the signing threshold whose attestation has expired can exploit this window without requiring collusion above threshold. [9](#0-8) 

### Recommendation
Governance voting functions should validate that the caller's TEE attestation is currently valid before accepting their vote, consistent with the guard used in `vote_pk` and `vote_reshared`. Additionally, when a participant's attestation expires (detectable at vote-cast time via re-verification), their previously stored votes in `CodeHashesVotes`, `LauncherHashVotes`, and `MeasurementVotes` should be invalidated — analogous to how `clean_non_participant_votes` purges votes after resharing removes a participant.

### Proof of Concept
1. Network has 3 participants (P1, P2, P3) with governance threshold = 2.
2. P1 submits an attestation with `expiry_timestamp_seconds = T`.
3. At time `T-1`, P1 calls `vote_add_os_measurement(malicious_measurement)`. Vote is stored and counted (1 vote).
4. Time advances past `T`. P1's attestation is now expired, but `verify_tee` has not been called.
5. P2 calls `vote_add_os_measurement(malicious_measurement)`. `MeasurementVotes::count_votes` counts both P1's and P2's votes → total = 2 ≥ threshold(2).
6. The malicious measurement is added to `allowed_measurements`, allowing nodes running an unverified OS measurement to submit valid attestations and join the network. [10](#0-9) [11](#0-10)

### Citations

**File:** crates/contract/src/lib.rs (L1102-1115)
```rust
    #[handle_result]
    pub fn vote_pk(
        &mut self,
        key_event_id: KeyEventId,
        public_key: dtos::PublicKey,
    ) -> Result<(), Error> {
        log!(
            "vote_pk: signer={}, key_event_id={:?}, public_key={:?}",
            env::signer_account_id(),
            key_event_id,
            public_key,
        );

        self.assert_caller_is_attested_participant_and_protocol_active();
```

**File:** crates/contract/src/lib.rs (L1467-1492)
```rust
    /// Vote to remove a launcher image hash from the allowed set. Requires ALL participants
    /// to vote for removal, since this invalidates attestations of nodes running that launcher.
    #[handle_result]
    pub fn vote_remove_launcher_hash(
        &mut self,
        launcher_hash: LauncherImageHash,
    ) -> Result<(), Error> {
        log!(
            "vote_remove_launcher_hash: signer={}, launcher_hash={:?}",
            env::signer_account_id(),
            launcher_hash,
        );
        self.voter_or_panic();

        let threshold_parameters = self.protocol_state.threshold_parameters_or_panic();

        let participant = AuthenticatedParticipantId::new(threshold_parameters.participants())?;
        let action = LauncherVoteAction::Remove(launcher_hash);
        let votes = self.tee_state.vote_launcher(action, &participant);

        // Removal requires ALL participants to vote
        let total_participants = threshold_parameters.participants().len() as u64;
        if votes >= total_participants {
            let removed = self.tee_state.remove_launcher_image(&launcher_hash);
            log!("launcher hash remove result: {}", removed);
        }
```

**File:** crates/contract/src/lib.rs (L1499-1516)
```rust
    pub fn vote_add_os_measurement(
        &mut self,
        measurement: ContractExpectedMeasurements,
    ) -> Result<(), Error> {
        log!(
            "vote_add_os_measurement: signer={}, measurement={:?}",
            env::signer_account_id(),
            measurement,
        );
        self.voter_or_panic();

        let threshold_parameters = self.protocol_state.threshold_parameters_or_panic();

        let participant = AuthenticatedParticipantId::new(threshold_parameters.participants())?;
        let action = MeasurementVoteAction::Add(measurement.clone());
        let votes = self.tee_state.vote_measurement(action, &participant);

        if votes >= self.threshold()?.value() {
```

**File:** crates/contract/src/lib.rs (L1527-1551)
```rust
    pub fn vote_remove_os_measurement(
        &mut self,
        measurement: ContractExpectedMeasurements,
    ) -> Result<(), Error> {
        log!(
            "vote_remove_os_measurement: signer={}, measurement={:?}",
            env::signer_account_id(),
            measurement,
        );
        self.voter_or_panic();

        let threshold_parameters = self.protocol_state.threshold_parameters_or_panic();

        let participant = AuthenticatedParticipantId::new(threshold_parameters.participants())?;
        let action = MeasurementVoteAction::Remove(measurement.clone());
        let votes = self.tee_state.vote_measurement(action, &participant);

        // Removal requires ALL participants to vote
        let total_participants = threshold_parameters.participants().len() as u64;
        if votes >= total_participants {
            let removed = self.tee_state.remove_measurement(&measurement);
            log!("OS measurement remove result: {}", removed);
        }

        Ok(())
```

**File:** crates/contract/src/lib.rs (L1693-1710)
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
```

**File:** crates/contract/src/tee/measurements.rs (L40-55)
```rust
    pub fn vote(
        &mut self,
        action: MeasurementVoteAction,
        participant: &AuthenticatedParticipantId,
    ) -> u64 {
        if self
            .vote_by_account
            .insert(participant.clone(), action.clone())
            .is_some()
        {
            log!("removed old measurement vote for signer");
        }
        let total = self.count_votes(&action);
        log!("total measurement votes for action: {}", total);
        total
    }
```

**File:** crates/contract/src/tee/measurements.rs (L57-66)
```rust
    /// Counts the total number of participants who have voted for the given action.
    fn count_votes(&self, action: &MeasurementVoteAction) -> u64 {
        u64::try_from(
            self.vote_by_account
                .values()
                .filter(|a| *a == action)
                .count(),
        )
        .expect("participant count should not overflow u64")
    }
```

**File:** crates/contract/src/tee/proposal.rs (L111-120)
```rust
    /// Counts the total number of participants who have voted for the given action.
    fn count_votes(&self, action: &LauncherVoteAction) -> u64 {
        u64::try_from(
            self.vote_by_account
                .values()
                .filter(|a| *a == action)
                .count(),
        )
        .expect("participant count should not overflow u64")
    }
```

**File:** crates/contract/src/tee/tee_state.rs (L359-379)
```rust
    /// Casts a vote for adding or removing an OS measurement.
    /// Returns the total number of votes for the same action.
    pub fn vote_measurement(
        &mut self,
        action: MeasurementVoteAction,
        participant: &AuthenticatedParticipantId,
    ) -> u64 {
        self.measurement_votes.vote(action, participant)
    }

    /// Adds a new measurement set to the allowed list. Clears measurement votes.
    pub fn add_measurement(&mut self, measurement: ContractExpectedMeasurements) -> bool {
        self.measurement_votes.clear_votes();
        self.allowed_measurements.add(measurement)
    }

    /// Removes a measurement set from the allowed list. Clears measurement votes.
    pub fn remove_measurement(&mut self, measurement: &ContractExpectedMeasurements) -> bool {
        self.measurement_votes.clear_votes();
        self.allowed_measurements.remove(measurement)
    }
```

**File:** crates/contract/src/tee/tee_state.rs (L393-400)
```rust
    /// Drops votes cast by nodes that are no longer participants. Used after a resharing
    /// concludes. Attestation cleanup is handled separately by
    /// [`TeeState::clean_invalid_attestations`].
    pub fn clean_non_participant_votes(&mut self, participants: &Participants) {
        self.votes = self.votes.get_remaining_votes(participants);
        self.launcher_votes = self.launcher_votes.get_remaining_votes(participants);
        self.measurement_votes = self.measurement_votes.get_remaining_votes(participants);
    }
```

**File:** crates/mpc-attestation/src/attestation.rs (L226-236)
```rust
                expiry_timestamp_seconds: expiration_timestamp_seconds,
                measurements,
            }) => {
                let attestation_has_expired = *expiration_timestamp_seconds < timestamp_seconds;

                if attestation_has_expired {
                    return Err(VerificationError::Custom(format!(
                        "The attestation expired at t = {:?}, time_now = {:?}",
                        expiration_timestamp_seconds, timestamp_seconds
                    )));
                }
```
