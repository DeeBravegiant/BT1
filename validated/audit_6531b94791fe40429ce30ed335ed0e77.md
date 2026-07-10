### Title
Stale Votes from Removed Participants Inflate TEE Governance Vote Counts After Resharing — (`File: crates/contract/src/lib.rs`)

### Summary

After a resharing completes, the contract spawns several detached cleanup promises, including `clean_tee_status`, to remove stale votes from participants who were dropped. If that promise fails silently, stale votes from removed Byzantine participants persist in `tee_state.votes`. Unlike `vote_update`, which explicitly re-filters votes against the current participant set on every call, the TEE governance functions (`vote_code_hash`, `vote_add_launcher_hash`, `vote_add_os_measurement`) use the raw accumulated vote count without filtering. This allows stale pre-resharing votes to count toward the threshold, enabling premature whitelisting of a malicious TEE image or OS measurement with fewer current-participant votes than the threshold requires.

### Finding Description

When resharing concludes in `vote_reshared`, the contract transitions `protocol_state` to the new `Running` state and then spawns six detached cleanup promises: [1](#0-0) 

All six are `.detach()`ed — fire-and-forget. If `clean_tee_status` fails (e.g., due to insufficient gas for a large participant set), stale votes from removed participants remain in `tee_state.votes.proposal_by_account`.

The developers are explicitly aware that cleanup can fail. `vote_update` has a compensating defense — it re-filters votes against the current participant set on every invocation: [2](#0-1) 

The comment at line 1176 reads: *"Note: MpcContract::vote_update uses filtering to ensure correctness even if this cleanup fails."*

However, the TEE governance voting functions carry no equivalent defense. `vote_code_hash` simply calls `self.tee_state.vote(code_hash, &participant)` and compares the raw returned count to the threshold: [3](#0-2) 

`vote_add_launcher_hash` and `vote_add_os_measurement` follow the identical pattern: [4](#0-3) [5](#0-4) 

The unit test `test_clean_non_participant_votes_removes_stale_votes` explicitly confirms that without cleanup, stale votes from removed participants are counted: [6](#0-5) 

### Impact Explanation

If a malicious TEE image hash is whitelisted, nodes running that image can submit valid attestations via `submit_participant_info` and be accepted as participants. A backdoored TEE image could extract key shares during DKG or resharing, enabling unauthorized threshold signature issuance or key share recovery. This maps to the **Critical** impact class: *unauthorized access to MPC key shares or signing capability that materially enables forgery or secret recovery*.

### Likelihood Explanation

The attack requires:
1. K Byzantine participants (K < threshold) vote for a malicious `code_hash` / `launcher_hash` / `os_measurement` before resharing.
2. A resharing removes those K participants.
3. The `clean_tee_status` detached promise fails silently (e.g., gas budget `clean_tee_status_tera_gas` is insufficient for the participant set size, or the promise is dropped under load).
4. T − K remaining/new participants vote for the same malicious hash, bringing the total to T (stale K + fresh T−K), crossing the threshold.

Step 3 is the critical enabler. The gas budget is configurable and defaults to a small constant; with a growing participant set, the cleanup can silently fail. Steps 1–2 require only sub-threshold Byzantine collusion, which is explicitly within scope.

### Recommendation

Apply the same vote-filtering defense used in `vote_update` to all TEE governance voting functions. Before comparing the vote count to the threshold, re-count only votes cast by accounts that are in the current participant set:

```rust
// In vote_code_hash, vote_add_launcher_hash, vote_add_os_measurement, etc.:
let valid_votes = threshold_parameters
    .participants()
    .participants()
    .iter()
    .filter(|(account_id, _, _)| {
        self.tee_state.votes.proposal_by_account
            .get(account_id)
            .is_some_and(|voted_hash| *voted_hash == code_hash)
    })
    .count() as u64;

if valid_votes >= self.threshold()?.value() {
    self.tee_state.whitelist_tee_proposal(code_hash, tee_upgrade_deadline_duration);
}
```

This makes correctness independent of whether the post-resharing cleanup promise succeeded, exactly as `vote_update` already does.

### Proof of Concept

1. Network: 5 participants (P0–P4), threshold T = 3.
2. P0 and P1 (Byzantine, K = 2 < T) call `vote_code_hash(malicious_hash)`. `tee_state.votes` now has 2 entries for `malicious_hash`.
3. Resharing removes P0 and P1. New participant set: {P2, P3, P4}.
4. The `clean_tee_status` detached promise fails (e.g., gas exhausted). Stale votes for P0 and P1 remain.
5. P2 calls `vote_code_hash(malicious_hash)`. `self.tee_state.vote(malicious_hash, &p2_auth)` returns 3 (2 stale + 1 new).
6. `3 >= threshold (3)` → `whitelist_tee_proposal(malicious_hash, ...)` executes.
7. Nodes running the malicious image can now submit valid attestations and join the network.

### Citations

**File:** crates/contract/src/lib.rs (L1170-1235)
```rust
        if let Some(new_state) = self.protocol_state.vote_reshared(key_event_id)? {
            // Resharing has concluded, transition to running state
            self.protocol_state = new_state;
            self.recompute_available_foreign_chains();

            // Spawn a promise to clean up votes from non-participants.
            // Note: MpcContract::vote_update uses filtering to ensure correctness even if this cleanup fails.
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::REMOVE_NON_PARTICIPANT_UPDATE_VOTES.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(self.config.remove_non_participant_update_votes_tera_gas),
                )
                .detach();
            // Spawn a promise to drop votes cast by non-participants.
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::CLEAN_TEE_STATUS.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(self.config.clean_tee_status_tera_gas),
                )
                .detach();
            // Spawn a bounded sweep over stored attestations to prune invalid / expired entries.
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::CLEAN_INVALID_ATTESTATIONS.to_string(),
                    serde_json::to_vec(&serde_json::json!({
                        "max_scan": RESHARE_CLEAN_INVALID_ATTESTATIONS_MAX_SCAN
                    }))
                    .unwrap(),
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(self.config.clean_invalid_attestations_tera_gas),
                )
                .detach();
            // Spawn a promise to clean up orphaned node migrations for non-participants
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::CLEANUP_ORPHANED_NODE_MIGRATIONS.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(self.config.cleanup_orphaned_node_migrations_tera_gas),
                )
                .detach();
            // Spawn a promise to clean up foreign chain data for non-participants
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::CLEAN_FOREIGN_CHAIN_DATA.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(self.config.clean_foreign_chain_data_tera_gas),
                )
                .detach();
            // Spawn a promise to drop verifier-change votes cast by non-participants
            Promise::new(env::current_account_id())
                .function_call(
                    method_names::REMOVE_NON_PARTICIPANT_TEE_VERIFIER_VOTES.to_string(),
                    vec![],
                    NearToken::from_yoctonear(0),
                    Gas::from_tgas(
                        self.config
                            .remove_non_participant_tee_verifier_votes_tera_gas,
                    ),
                )
                .detach();
```

**File:** crates/contract/src/lib.rs (L1362-1374)
```rust
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
```

**File:** crates/contract/src/lib.rs (L1407-1431)
```rust
    pub fn vote_code_hash(&mut self, code_hash: NodeImageHash) -> Result<(), Error> {
        log!(
            "vote_code_hash: signer={}, code_hash={:?}",
            env::signer_account_id(),
            code_hash,
        );
        self.voter_or_panic();

        let threshold_parameters = self.protocol_state.threshold_parameters_or_panic();

        let participant = AuthenticatedParticipantId::new(threshold_parameters.participants())?;
        let votes = self.tee_state.vote(code_hash, &participant);

        let tee_upgrade_deadline_duration =
            Duration::from_secs(self.config.tee_upgrade_deadline_duration_seconds);

        // If the vote threshold is met and the new Docker hash is allowed by the TEE's RTMR3,
        // update the state
        if votes >= self.threshold()?.value() {
            self.tee_state
                .whitelist_tee_proposal(code_hash, tee_upgrade_deadline_duration);
        }

        Ok(())
    }
```

**File:** crates/contract/src/lib.rs (L1437-1465)
```rust
    pub fn vote_add_launcher_hash(
        &mut self,
        launcher_hash: LauncherImageHash,
    ) -> Result<(), Error> {
        log!(
            "vote_add_launcher_hash: signer={}, launcher_hash={:?}",
            env::signer_account_id(),
            launcher_hash,
        );
        self.voter_or_panic();

        let threshold_parameters = self.protocol_state.threshold_parameters_or_panic();

        let participant = AuthenticatedParticipantId::new(threshold_parameters.participants())?;
        let action = LauncherVoteAction::Add(launcher_hash);
        let votes = self.tee_state.vote_launcher(action, &participant);

        let tee_upgrade_deadline_duration =
            Duration::from_secs(self.config.tee_upgrade_deadline_duration_seconds);

        if votes >= self.threshold()?.value() {
            let added = self
                .tee_state
                .add_launcher_image(launcher_hash, tee_upgrade_deadline_duration);
            log!("launcher hash add result: {}", added);
        }

        Ok(())
    }
```

**File:** crates/contract/src/lib.rs (L1497-1522)
```rust
    /// Vote to add a new OS measurement set to the allowed list. Requires threshold votes.
    #[handle_result]
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
