### Title
Stale `MeasurementVotes` Entry from Removed Participant Reduces Effective Threshold for OS Measurement Approval — (`crates/contract/src/tee/measurements.rs`, `crates/contract/src/lib.rs`)

---

### Summary

`MeasurementVotes::count_votes` counts all entries in `vote_by_account` without filtering by the current participant set. After a resharing removes a Byzantine participant P_evil, P_evil's vote persists in the map until the detached `clean_tee_status` promise executes (next block). During that window, T−1 Byzantine participants in the new set can vote for the same malicious measurement, causing `count_votes` to return T and triggering `add_measurement`. The effective threshold for OS measurement approval is reduced from T to T−1 current-participant votes.

---

### Finding Description

**Root cause — `MeasurementVotes::count_votes` is unfiltered:** [1](#0-0) 

The function iterates all `vote_by_account` values with no participant-membership check. This is the same structural flaw that was explicitly fixed in `vote_update`: [2](#0-1) 

That fix carries the comment *"This ensures correctness even if the cleanup promise in `MpcContract::vote_reshared()` fails."* No equivalent filter was applied to `vote_add_os_measurement`.

**`vote_add_os_measurement` uses the raw count:** [3](#0-2) 

The `votes` value returned by `vote_measurement` → `MeasurementVotes::vote` → `count_votes` is compared directly against `self.threshold()` with no post-filter.

**`clean_tee_status` is a detached promise, not atomic:** [4](#0-3) 

The `.detach()` call means `clean_tee_status` executes as a separate receipt in a subsequent block. Between `vote_reshared` completing and that receipt landing, P_evil's entry remains in `measurement_votes.vote_by_account`.

**`clean_non_participant_votes` does remove the stale entry — but only when it runs:** [5](#0-4) 

**`authenticate_update_vote` during Resharing checks old participants:** [6](#0-5) 

P_evil can cast its vote while the contract is still in Resharing state (old participant set), before the transition to Running completes.

---

### Attack Sequence

Setup: N participants, threshold T. P_evil is one participant. T−1 other Byzantine participants exist.

1. **P_evil votes `Add(M_evil)`** while the contract is in Resharing state (old participants are still valid voters). P_evil's entry is stored in `measurement_votes.vote_by_account`.
2. **Resharing completes** (`vote_reshared` transitions to Running, removing P_evil). `clean_tee_status` is spawned as a detached promise.
3. **Race window opens** — P_evil's stale vote is still in the map.
4. **T−1 Byzantine participants in the new set call `vote_add_os_measurement(M_evil)`**. Each call is accepted (`voter_or_panic` passes because they are current participants). `count_votes` now returns T−1 (new) + 1 (P_evil stale) = T.
5. **`votes >= self.threshold()` is true** → `add_measurement(M_evil)` executes, whitelisting the malicious OS measurement.
6. `clean_tee_status` executes in the next block — too late.

---

### Impact Explanation

A malicious OS measurement set is added to the on-chain allowlist. Any node presenting a TDX attestation quoting M_evil will pass `verify_tee` / `submit_participant_info`. This is an attestation authorization bypass: nodes running attacker-controlled OS images can be admitted as participants, undermining the TEE security model that gates participation in threshold signing.

---

### Likelihood Explanation

The attack requires:
- 1 Byzantine participant who is removed by resharing (P_evil)
- T−1 Byzantine participants who remain in the new set

Total Byzantine participants: T (1 removed + T−1 remaining). This is exactly threshold-many Byzantine participants in aggregate, but only T−1 are in the *current* participant set — below the threshold that honest participants enforce. The race window is one NEAR block (~1 second), which is sufficient for pre-staged transactions. The `vote_update` fix and its comment confirm the developers are aware of this class of race but did not apply the same fix to OS measurement voting.

---

### Recommendation

Apply the same participant-filtering pattern used in `vote_update` to `vote_add_os_measurement` (and analogously to `vote_add_launcher_hash` / `vote_code_hash`):

```rust
// In vote_add_os_measurement, replace:
if votes >= self.threshold()?.value() { ... }

// With:
let running = /* get Running state */;
let valid_votes = running.parameters.participants()
    .participants()
    .iter()
    .filter(|(account_id, _, _)| {
        self.tee_state.measurement_votes.vote_by_account
            .iter()
            .any(|(auth_id, action)| {
                participants.account_id_for(auth_id) == account_id
                    && *action == MeasurementVoteAction::Add(measurement.clone())
            })
    })
    .count() as u64;
if valid_votes >= self.threshold()?.value() { ... }
```

Alternatively, `MeasurementVotes::count_votes` should accept a `&Participants` argument and filter internally, mirroring `ThresholdParametersVotes::n_votes`.

---

### Proof of Concept

Mirror the existing `test_clean_non_participant_votes_removes_stale_votes` test pattern: [7](#0-6) 

Extend it for `MeasurementVotes`: have P_evil vote `Add(M_evil)`, simulate resharing (update participant set without calling `clean_non_participant_votes`), then have T−1 remaining participants vote `Add(M_evil)`. Assert that `vote_measurement` returns T and that `add_measurement` would be triggered — demonstrating the stale vote inflates the count past threshold without `clean_tee_status` having run.

### Citations

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

**File:** crates/contract/src/lib.rs (L1185-1193)
```rust
            // Spawn a promise to drop votes cast by non-participants.
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::CLEAN_TEE_STATUS.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(self.config.clean_tee_status_tera_gas),
                )
                .detach();
```

**File:** crates/contract/src/lib.rs (L1361-1378)
```rust
        // Filter votes to only count current participants voting for this specific update.
        // This ensures correctness even if the cleanup promise in MpcContract::vote_reshared() fails.
        let valid_votes_count = running_state
            .parameters
            .participants()
            .participants()
            .iter()
            .filter(|(account_id, _, _)| {
                self.proposed_updates
                    .vote_by_participant
                    .get(account_id)
                    .is_some_and(|voted_id| *voted_id == id)
            })
            .count();

        // Not enough votes from current participants, wait for more.
        if (valid_votes_count as u64) < threshold.value() {
            return Ok(false);
```

**File:** crates/contract/src/lib.rs (L1499-1522)
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
            let added = self.tee_state.add_measurement(measurement);
            log!("OS measurement add result: {}", added);
        }

        Ok(())
    }
```

**File:** crates/contract/src/tee/tee_state.rs (L396-400)
```rust
    pub fn clean_non_participant_votes(&mut self, participants: &Participants) {
        self.votes = self.votes.get_remaining_votes(participants);
        self.launcher_votes = self.launcher_votes.get_remaining_votes(participants);
        self.measurement_votes = self.measurement_votes.get_remaining_votes(participants);
    }
```

**File:** crates/contract/src/tee/tee_state.rs (L1496-1545)
```rust
    /// Stale CodeHashesVotes entries from removed participants must not count toward
    /// quorum after resharing.
    ///
    /// Scenario (N=5, T=3):
    /// 1. P1 and P2 vote for malicious hash before resharing.
    /// 2. Resharing removes P1 and P2. New set: {P3, P4, P5}.
    /// 3. clean_non_participant_votes removes stale votes.
    /// 4. P3 votes for the same hash — only 1 vote, not 3.
    #[test]
    fn test_clean_non_participant_votes_removes_stale_votes() {
        // Build 5 participants
        let mut all_participants = Participants::new();
        let mut account_ids = Vec::new();
        for i in 0..5 {
            let (account_id, info) = gen_participant(i);
            account_ids.push(account_id.clone());
            all_participants.insert(account_id, info).unwrap();
        }

        let mut tee_state = TeeState::default();

        // P0 and P1 vote for a malicious hash before resharing
        let malicious_hash = NodeImageHash::from([0xAA; 32]);
        for account_id in &account_ids[0..2] {
            let mut ctx = VMContextBuilder::new();
            ctx.signer_account_id(account_id.clone());
            testing_env!(ctx.build());
            let auth_id = AuthenticatedParticipantId::new(&all_participants).unwrap();
            tee_state.votes.vote(malicious_hash, &auth_id);
        }
        assert_eq!(tee_state.votes.proposal_by_account.len(), 2);

        // Resharing removes P0 and P1. New participant set: {P2, P3, P4}.
        let new_participants = all_participants.subset(2..5);

        // Clean non-participants (as done by CLEAN_TEE_STATUS after resharing)
        tee_state.clean_non_participant_votes(&new_participants);

        // Stale votes must be removed
        assert_eq!(tee_state.votes.proposal_by_account.len(), 0);

        // P2 votes for the same malicious hash — should be only 1 vote, not 3
        let p2_account = &account_ids[2];
        let mut ctx = VMContextBuilder::new();
        ctx.signer_account_id(p2_account.clone());
        testing_env!(ctx.build());
        let auth_id = AuthenticatedParticipantId::new(&new_participants).unwrap();
        let vote_count = tee_state.votes.vote(malicious_hash, &auth_id);
        assert_eq!(vote_count, 1, "Only the fresh vote from P2 should count");
    }
```

**File:** crates/contract/src/state.rs (L232-235)
```rust
            ProtocolContractState::Resharing(state) => {
                AuthenticatedParticipantId::new(
                    state.previous_running_state.parameters.participants(),
                )?;
```
