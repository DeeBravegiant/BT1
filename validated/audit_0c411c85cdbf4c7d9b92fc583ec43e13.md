### Title
Stale Code-Hash Votes from Removed Participants Bypass Whitelisting Threshold After Resharing — (File: `crates/contract/src/tee/tee_state.rs`)

---

### Summary

When a resharing completes and removes participants, their previously cast `vote_code_hash` votes remain in `tee_state.votes.proposal_by_account` until an asynchronous, detached cleanup promise (`CLEAN_TEE_STATUS`) executes. In the receipt-processing window between resharing completion and cleanup, a colluding remaining participant can cast a vote that — combined with the stale votes — reaches the governance threshold and whitelists a malicious Docker image hash without the required number of *current* participants agreeing.

---

### Finding Description

**Root cause — asynchronous cleanup of stale votes**

When `vote_reshared` finalises a resharing, it spawns `CLEAN_TEE_STATUS` as a *detached* promise:

```rust
Promise::new(env::current_account_id())
    .function_call(
        method_names::CLEAN_TEE_STATUS.to_string(),
        vec![],
        NearToken::from_yoctonear(0),
        Gas::from_tgas(self.config.clean_tee_status_tera_gas),
    )
    .detach();
``` [1](#0-0) 

A detached promise creates a new receipt that is queued *after* the current receipt. Any other receipt that lands in the same block before the cleanup receipt executes will observe the un-cleaned state.

**Stale votes are counted without participant filtering**

The `CodeHashesVotes::vote()` method (called by `vote_code_hash`) counts **all** entries in `proposal_by_account`, regardless of whether the voter is still a current participant. The test `test_clean_non_participant_votes_removes_stale_votes` makes this explicit:

```rust
// P0 and P1 vote for a malicious hash before resharing
// ...
// Resharing removes P0 and P1. New participant set: {P2, P3, P4}.
tee_state.clean_non_participant_votes(&new_participants);
// Stale votes must be removed
assert_eq!(tee_state.votes.proposal_by_account.len(), 0);

// P2 votes for the same malicious hash — should be only 1 vote, not 3
let vote_count = tee_state.votes.vote(malicious_hash, &auth_id);
assert_eq!(vote_count, 1, "Only the fresh vote from P2 should count");
``` [2](#0-1) 

The comment *"should be only 1 vote, not 3"* confirms that **without cleanup**, the count would include the two stale votes from removed participants.

**Contrast with `vote_update`'s explicit defence**

`vote_update` was hardened against exactly this race by re-filtering votes against the live participant set at count time:

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
``` [3](#0-2) 

No equivalent guard exists for `vote_code_hash`. The comment itself acknowledges the cleanup can fail, yet `vote_code_hash` has no fallback.

---

### Impact Explanation

A malicious Docker image hash is whitelisted with fewer than `threshold` current participants colluding. Nodes running the malicious image can then pass `submit_participant_info` attestation checks, be accepted as MPC participants, and participate in threshold signing or resharing — enabling unauthorized signature issuance or key-share exposure.

This matches: **High — participant/attestation authorization bypass** and potentially **Critical — unauthorized access to MPC key shares or signing capability**.

---

### Likelihood Explanation

**Medium.** The attacker must:
1. Control participants who will be removed in an upcoming resharing (Byzantine participants strictly below the signing threshold of *current* participants).
2. Pre-position `vote_code_hash` calls for a malicious hash before resharing (while they are still valid participants).
3. Have at least one colluding *remaining* participant submit a vote in the window between resharing completion and cleanup receipt execution.

The timing window is at least one NEAR block (the detached receipt is queued after the current receipt). An attacker monitoring the chain can submit the colluding vote in the same block as the resharing-completion transaction, before the cleanup receipt is processed.

---

### Recommendation

Apply the same live-filtering defence used in `vote_update` to `vote_code_hash` (and all other threshold-gated TEE voting functions): when counting votes toward the threshold, iterate the *current* participant set and count only entries whose voter is still a member, rather than relying on the asynchronous cleanup promise.

Alternatively, make `CLEAN_TEE_STATUS` a synchronous call (not detached) within `vote_reshared`, so stale votes are removed atomically before the new Running state is observable.

---

### Proof of Concept

**Setup:** N = 5, T = 3. Participants: P1, P2, P3, P4, P5. Attacker controls P1, P2 (to be removed) and P3 (remaining).

1. **Before resharing:** P1 and P2 each call `vote_code_hash(malicious_hash)`. Two entries are stored in `tee_state.votes.proposal_by_account`. Both calls succeed because P1 and P2 are current participants.

2. **Resharing:** A `vote_new_parameters` proposal removes P1 and P2. All participants vote; `vote_reshared` is called by the new set. The contract transitions to Running with {P3, P4, P5}, T = 3. `CLEAN_TEE_STATUS` is spawned as a detached promise — its receipt is queued but not yet executed.

3. **Exploit window (same block, before cleanup receipt):** P3 calls `vote_code_hash(malicious_hash)`. `CodeHashesVotes::vote()` counts all stored entries: P1 (stale) + P2 (stale) + P3 (current) = **3 votes = threshold**. The malicious hash is added to the whitelist.

4. **Cleanup runs (next receipt):** `clean_non_participant_votes` removes P1 and P2's entries — but the malicious hash is already whitelisted.

5. **Consequence:** A node running the malicious image submits `submit_participant_info` with a valid attestation for the now-whitelisted hash. It is accepted as a participant and can participate in signing or resharing. [4](#0-3) [2](#0-1) [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L1161-1238)
```rust
    pub fn vote_reshared(&mut self, key_event_id: KeyEventId) -> Result<(), Error> {
        log!(
            "vote_reshared: signer={}, resharing_id={:?}",
            env::signer_account_id(),
            key_event_id,
        );

        self.assert_caller_is_attested_participant_and_protocol_active();

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
        }

        Ok(())
```

**File:** crates/contract/src/lib.rs (L1336-1387)
```rust
    /// Vote for a proposed update given the [`UpdateId`] of the update.
    ///
    /// Returns `Ok(true)` if the amount of voters surpassed the threshold and the update was
    /// executed. Returns `Ok(false)` if the amount of voters did not surpass the threshold.
    /// Returns [`Error`] if the update was not found or if the voter is not a participant
    /// in the protocol.
    #[handle_result]
    pub fn vote_update(&mut self, id: UpdateId) -> Result<bool, Error> {
        log!(
            "vote_update: signer={}, id={:?}",
            env::signer_account_id(),
            id,
        );

        let ProtocolContractState::Running(running_state) = &self.protocol_state else {
            env::panic_str("protocol must be in running state");
        };

        let threshold = self.threshold()?;

        let voter = self.voter_or_panic();
        if self.proposed_updates.vote(&id, voter).is_none() {
            return Err(InvalidParameters::UpdateNotFound.into());
        }

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
        }

        let update_gas_deposit = Gas::from_tgas(self.config.contract_upgrade_deposit_tera_gas);

        let Some(_promise) = self.proposed_updates.do_update(&id, update_gas_deposit) else {
            return Err(InvalidParameters::UpdateNotFound.into());
        };

        Ok(true)
```

**File:** crates/contract/src/tee/tee_state.rs (L1496-1544)
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
```
