### Title
Stale Participant Votes Persist After Resharing Due to Detached Fire-and-Forget Cleanup Promises — (File: `crates/contract/src/lib.rs`)

---

### Summary

When resharing completes via `vote_reshared`, the contract transitions to a new Running state with a changed participant set, then spawns all post-resharing cleanup work as **detached (fire-and-forget) promises**. If any cleanup promise fails silently (e.g., gas exhaustion for a large participant set), stale governance votes from removed participants remain in contract storage and continue to count toward thresholds for `vote_code_hash`, `vote_add_launcher_hash`, `vote_add_os_measurement`, and `vote_tee_verifier_change`. A Byzantine participant below the signing threshold can pre-cast a vote for a malicious Docker image hash before being removed, and if cleanup fails, that stale vote counts alongside new-participant votes to reach the governance threshold.

---

### Finding Description

In `vote_reshared()`, once resharing concludes, the contract spawns six cleanup calls as `.detach()`ed promises — fire-and-forget with no retry or failure propagation: [1](#0-0) 

The gas budgets are fixed at contract-config defaults: [2](#0-1) 

`clean_tee_status` (10 Tgas) is the promise responsible for removing stale code-hash, launcher-hash, and measurement votes from removed participants: [3](#0-2) 

The existing test explicitly documents that **without this cleanup, stale votes count toward thresholds**: [4](#0-3) 

The code comment in `vote_reshared` acknowledges the risk only for `vote_update`: [5](#0-4) 

No equivalent filtering guard exists in `vote_code_hash`, `vote_add_launcher_hash`, `vote_add_os_measurement`, or `vote_tee_verifier_change`. Those functions authenticate the **caller** as a current participant but do not filter out already-stored stale votes from removed participants when tallying the count.

This is the direct analog of the EIP-7579 finding: `onUninstall()` missing `delete walletSessionKeys[msg.sender]` — the lifecycle transition (resharing completion) does not atomically remove all state belonging to the departing participants; it delegates that removal to detachable side-effects that can silently fail.

---

### Impact Explanation

If `clean_tee_status` (or `remove_non_participant_tee_verifier_votes`) fails after resharing:

1. Stale votes from removed participants remain in `tee_state.votes`, `tee_state.launcher_votes`, `tee_state.measurement_votes`, and `tee_verifier_votes`.
2. These stale votes are counted alongside fresh votes from current participants when tallying `vote_code_hash`, `vote_add_launcher_hash`, `vote_add_os_measurement`, and `vote_tee_verifier_change`.
3. A removed participant's pre-cast vote for a malicious Docker image hash can combine with a single new-participant vote to cross the governance threshold — approving the hash with fewer honest-participant votes than the protocol requires.
4. Once a malicious image hash is whitelisted, a node running that image passes `submit_participant_info` attestation checks and is admitted as an attested participant, gaining the ability to call `respond`, `respond_ckd`, and `respond_verify_foreign_tx` — the endpoints that deliver threshold signatures and confidential key derivation outputs to users.

This breaks the production safety invariant that governance decisions require the **current** participant set's threshold votes, and can escalate to unauthorized signing capability if the malicious image

### Citations

**File:** crates/contract/src/lib.rs (L1175-1235)
```rust
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

**File:** crates/contract/src/config.rs (L24-36)
```rust
/// Prepaid gas for a `clean_tee_status` call
const DEFAULT_CLEAN_TEE_STATUS_TERA_GAS: u64 = 10;
/// Prepaid gas for the reshare-time `clean_invalid_attestations` promise.
const DEFAULT_CLEAN_INVALID_ATTESTATIONS_TERA_GAS: u64 = 10;
/// Prepaid gas for a `cleanup_orphaned_node_migrations` call
/// TODO(#1164): benchmark
const DEFAULT_CLEANUP_ORPHANED_NODE_MIGRATIONS_TERA_GAS: u64 = 4;
/// Prepaid gas for a `remove_non_participant_update_votes` call
const DEFAULT_REMOVE_NON_PARTICIPANT_UPDATE_VOTES_TERA_GAS: u64 = 5;
/// Prepaid gas for a `clean_foreign_chain_data` call
const DEFAULT_CLEAN_FOREIGN_CHAIN_DATA_TERA_GAS: u64 = 5;
/// Prepaid gas for a `remove_non_participant_tee_verifier_votes` call
const DEFAULT_REMOVE_NON_PARTICIPANT_TEE_VERIFIER_VOTES_TERA_GAS: u64 = 5;
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
