### Title
Stale Votes from Removed Participants Bypass Unanimity Requirement for `vote_remove_launcher_hash` / `vote_remove_os_measurement` — (File: crates/contract/src/lib.rs)

---

### Summary

After a resharing that removes participants, the `clean_tee_status` cleanup is a **detached promise** that can fail silently. If it fails, votes cast by removed participants persist in `launcher_votes` and `measurement_votes`. The `vote_remove_launcher_hash` and `vote_remove_os_measurement` functions count all stored votes without filtering out non-participants, unlike `vote_update` which has an explicit compensating filter. This allows a subset of current participants — fewer than unanimity — to remove a launcher hash or OS measurement by leveraging stale votes from removed participants, breaking the unanimity invariant that protects nodes from having their attestations invalidated without full consent.

---

### Finding Description

`vote_remove_launcher_hash` and `vote_remove_os_measurement` both require **all** current participants to vote (unanimity), because removing a launcher hash or OS measurement invalidates attestations of nodes still running that configuration. [1](#0-0) 

The vote count is computed by `LauncherHashVotes::count_votes`, which iterates over the entire `vote_by_account` map without filtering to current participants: [2](#0-1) 

After resharing concludes in `vote_reshared`, the contract spawns a **detached** `clean_tee_status` promise to purge stale votes: [3](#0-2) 

The code comment immediately above this detached call explicitly acknowledges that the cleanup can fail and that `vote_update` has a compensating filter for this reason: [4](#0-3) 

`vote_update` applies an explicit per-call filter to count only current-participant votes: [5](#0-4) 

`vote_remove_launcher_hash` and `vote_remove_os_measurement` have **no such filter**. They compare the raw stored vote count against the current participant count: [6](#0-5) 

`clean_tee_status` calls `clean_non_participant_votes`, which does correctly prune all three vote maps — but only when the detached promise succeeds: [7](#0-6) 

---

### Impact Explanation

Removing a launcher hash invalidates the TEE attestations of every node still running that launcher, effectively ejecting them from the network. The unanimity requirement exists precisely to prevent a subset of participants from unilaterally invalidating others' attestations.

If stale votes from removed participants remain in `launcher_votes` after a failed `clean_tee_status` promise, the effective unanimity threshold is reduced. Specifically, if `R` removed participants had voted `Remove(H)` before resharing, only `M - R` of the `M` current participants need to vote to satisfy `votes >= M`. In the extreme case where `R = M - 1`, a single current participant can trigger removal.

This breaks the production safety invariant that launcher hash removal requires unanimous consent, and can cause honest nodes to lose their valid attestation status — preventing them from participating in threshold signing — without their agreement.

**Impact category:** Medium — participant-state manipulation that breaks a production safety invariant (unanimity for launcher/measurement removal) without requiring network-level DoS or operator misconfiguration.

---

### Likelihood Explanation

The preconditions are:
1. A resharing that removes at least one participant who had previously voted `Remove(launcher_hash)` — a plausible operational sequence during launcher upgrades.
2. The `clean_tee_status` detached promise fails (e.g., out of gas). The codebase explicitly acknowledges this failure mode in the comment above the detached call.

Both conditions are realistic in production. The developers already identified the failure mode for `vote_update` and added a compensating filter there, but did not apply the same fix to the launcher/measurement removal paths.

---

### Recommendation

Apply the same compensating filter used in `vote_update` to `vote_remove_launcher_hash` and `vote_remove_os_measurement`: before comparing the vote count against `total_participants`, filter `launcher_votes` / `measurement_votes` to count only votes from accounts that are current participants.

Alternatively, extend `LauncherHashVotes::count_votes` and `MeasurementVotes::count_votes` to accept a `&Participants` argument and filter internally, mirroring the `get_remaining_votes` logic already present on both types: [8](#0-7) 

---

### Proof of Concept

**Setup:** 5 participants `{P1, P2, P3, P4, P5}`, unanimity threshold = 5.

1. P1 and P2 call `vote_remove_launcher_hash(old_hash)` → 2 votes stored in `launcher_votes`.
2. A resharing removes P1 and P2; new participant set = `{P3, P4, P5}`.
3. `vote_reshared` transitions to Running and spawns the detached `clean_tee_status` promise.
4. The `clean_tee_status` promise runs out of gas and fails silently. Stale votes from P1 and P2 remain in `launcher_votes`.
5. P3 calls `vote_remove_launcher_hash(old_hash)`. `count_votes` returns 3 (P1 stale + P2 stale + P3 fresh). `total_participants` = 3. Check: `3 >= 3` → **true**.
6. `old_hash` is removed from the allowed launcher list.
7. P4 and P5, still running `old_hash`, now have invalid attestations. They are ejected from the network without having voted. [9](#0-8) [2](#0-1)

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

**File:** crates/contract/src/lib.rs (L1467-1495)
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

        Ok(())
    }
```

**File:** crates/contract/src/tee/proposal.rs (L112-120)
```rust
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

**File:** crates/contract/src/tee/proposal.rs (L127-140)
```rust
    /// Returns a new `LauncherHashVotes` containing only votes from current participants.
    pub fn get_remaining_votes(&self, participants: &Participants) -> Self {
        let remaining = self
            .vote_by_account
            .iter()
            .filter(|(participant_id, _)| {
                participants.is_participant_given_participant_id(&participant_id.get())
            })
            .map(|(participant_id, vote)| (participant_id.clone(), vote.clone()))
            .collect();
        LauncherHashVotes {
            vote_by_account: remaining,
        }
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
