### Title
Stale TEE Governance Votes from Removed Participants Count Toward Threshold After Detached `clean_tee_status` Cleanup - (File: `crates/contract/src/lib.rs`)

---

### Summary

When resharing completes in `vote_reshared`, the `clean_tee_status` promise is spawned with `.detach()`, meaning its success is never checked. If this cleanup fails (e.g., out-of-gas), stale TEE governance votes (`votes`, `launcher_votes`, `measurement_votes`) from removed participants persist in contract state. Because `vote_code_hash` and related TEE governance methods count **all** entries in `proposal_by_account` without filtering for current participants, these stale votes count toward the whitelisting threshold — allowing a malicious Docker image hash to be approved with fewer legitimate votes than the threshold requires.

---

### Finding Description

**Root cause — unchecked detached cleanup promise:**

In `vote_reshared`, when resharing concludes and the contract transitions to `Running`, six cleanup promises are spawned with `.detach()`: [1](#0-0) 

The `.detach()` call means the NEAR runtime fires the promise but the calling receipt does not wait for or check its result. If `clean_tee_status` runs out of gas or panics, the failure is silently swallowed and the state transition to `Running` is already committed.

**What `clean_tee_status` does:** [2](#0-1) 

It calls `clean_non_participant_votes`, which purges stale entries from all three TEE vote maps: [3](#0-2) 

**Why stale votes are dangerous — `count_votes` has no participant filter:**

`vote_code_hash` counts votes by iterating all values in `proposal_by_account` without checking whether the voter is still a current participant: [4](#0-3) 

The threshold check in `vote_code_hash` uses this unfiltered count directly: [5](#0-4) 

The same unfiltered counting applies to `launcher_votes` and `measurement_votes` used by `vote_add_launcher_hash`, `vote_remove_launcher_hash`, `vote_add_os_measurement`, and `vote_remove_os_measurement`.

**Contrast with `vote_update`:**

The developers explicitly acknowledged this pattern for update votes with the comment: [6](#0-5) 

> "Note: MpcContract::vote_update uses filtering to ensure correctness even if this cleanup fails."

No equivalent filtering exists in the TEE governance vote-counting path.

**The codebase's own test documents the exact attack scenario:** [7](#0-6) 

This test proves that without `clean_non_participant_votes`, stale votes from removed participants count toward the threshold.

---

### Impact Explanation

If `clean_tee_status` fails silently after resharing:

- Stale `votes` entries from removed participants remain in `tee_state.votes.proposal_by_account`.
- A single new participant voting for the same malicious Docker image hash can push the count over the threshold, because the stale votes are included in `count_votes`.
- A malicious Docker image hash gets added to the allowed list (`whitelist_tee_proposal`), permitting nodes running adversarial code to submit valid attestations and participate in the MPC network.
- Nodes running adversarial code can participate in threshold signing and key derivation, breaking the TEE security boundary.

This breaks the production safety invariant that only current participants' votes count toward governance thresholds — a **Medium** impact per the allowed scope (participant-state manipulation breaking safety/accounting invariants).

---

### Likelihood Explanation

**Medium.** The failure requires:

1. `clean_tee_status` to fail — possible if `config.clean_tee_status_tera_gas` is set too low, or if the NEAR runtime gas accounting changes. The gas budget is a configurable integer with no enforced lower bound.
2. Removed participants to have voted for a target hash before resharing — a normal operational scenario (participants routinely vote for image hashes during upgrades).
3. At least one new participant to vote for the same hash after resharing — only one colluding or compromised new participant is needed if two removed participants had already voted.

The scenario is realistic during routine TEE image upgrades that coincide with participant set changes.

---

### Recommendation

1. **Add participant filtering in `count_votes`** (analogous to `vote_update`'s filtering): pass the current `Participants` set into `count_votes` and skip entries whose `AuthenticatedParticipantId` no longer belongs to the current set.

2. **Or, check the result of `clean_tee_status`** by chaining it as a callback instead of `.detach()`ing it, and reverting the state transition if cleanup fails.

3. **Or, move the stale-vote check into `vote_code_hash`** (and the other TEE governance methods) by filtering `proposal_by_account` against `threshold_parameters.participants()` before counting, mirroring the explicit comment already applied to `vote_update`.

---

### Proof of Concept

```
Setup: N=5, T=3. Participants: {P1, P2, P3, P4, P5}.

1. P1 and P2 call vote_code_hash(malicious_hash) before resharing.
   → tee_state.votes.proposal_by_account = {P1_id: malicious_hash, P2_id: malicious_hash}

2. Resharing removes P1 and P2. New set: {P3, P4, P5}, T=3.
   → vote_reshared transitions to Running.
   → clean_tee_status is spawned with .detach() but runs out of gas and fails silently.
   → tee_state.votes.proposal_by_account still contains P1_id and P2_id entries.

3. P3 (one colluding new participant) calls vote_code_hash(malicious_hash).
   → count_votes(malicious_hash) iterates all values:
      P1_id → malicious_hash  (stale, but counted)
      P2_id → malicious_hash  (stale, but counted)
      P3_id → malicious_hash  (fresh)
      total = 3 >= threshold 3

4. whitelist_tee_proposal(malicious_hash) executes.
   → Nodes running the malicious image can now submit valid attestations
     and participate in threshold signing.
```

### Citations

**File:** crates/contract/src/lib.rs (L1175-1184)
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

**File:** crates/contract/src/lib.rs (L1425-1428)
```rust
        if votes >= self.threshold()?.value() {
            self.tee_state
                .whitelist_tee_proposal(code_hash, tee_upgrade_deadline_duration);
        }
```

**File:** crates/contract/src/lib.rs (L1803-1819)
```rust
    /// Private endpoint to drop votes cast by non-participants after resharing.
    /// Attestation cleanup is handled separately by [`MpcContract::clean_invalid_attestations`].
    #[private]
    #[handle_result]
    pub fn clean_tee_status(&mut self) -> Result<(), Error> {
        log!("clean_tee_status: signer={}", env::signer_account_id());

        let participants = match &self.protocol_state {
            ProtocolContractState::Running(state) => state.parameters.participants(),
            _ => {
                return Err(InvalidState::ProtocolStateNotRunning.into());
            }
        };

        self.tee_state.clean_non_participant_votes(participants);
        Ok(())
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

**File:** crates/contract/src/tee/proposal.rs (L46-52)
```rust
    /// Counts the total number of participants who have voted for the given code hash.
    fn count_votes(&self, proposal: &NodeImageHash) -> u64 {
        self.proposal_by_account
            .values()
            .filter(|&prop| prop == proposal)
            .count() as u64
    }
```
