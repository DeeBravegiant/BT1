### Title
Stale TEE Governance Votes from Removed Participants Persist After Resharing, Enabling Below-Threshold Code Hash Whitelisting — (File: `crates/contract/src/lib.rs`)

---

### Summary

After resharing completes, `vote_reshared` first transitions the protocol state (removing old participants from the active set), then spawns several detached (fire-and-forget) cleanup promises. One of these, `clean_tee_status`, removes stale TEE votes from non-participants. Because the promises are detached, their failure is silent. Critically, the TEE governance vote-counting functions (`vote_code_hash`, `vote_add_launcher_hash`, `vote_add_os_measurement`) count **all** stored votes without filtering by current participants — unlike `vote_update`, which explicitly filters. Stale votes from removed participants therefore persist and count toward the threshold, allowing a malicious code hash to be whitelisted with fewer current-participant votes than the threshold requires.

---

### Finding Description

**Step 1 — State transition before cleanup.**
In `vote_reshared`, when enough votes are collected, the contract immediately transitions `self.protocol_state` to the new `Running` state (with the new participant set, which excludes removed participants). Only after this transition are the cleanup promises spawned:

```rust
// crates/contract/src/lib.rs ~1170-1235
if let Some(new_state) = self.protocol_state.vote_reshared(key_event_id)? {
    self.protocol_state = new_state;          // ← old participants removed HERE
    ...
    Promise::new(env::current_account_id())
        .function_call(
            method_names::CLEAN_TEE_STATUS.to_string(), ...
        )
        .detach();                            // ← cleanup spawned AFTER, fire-and-forget
``` [1](#0-0) 

**Step 2 — Cleanup is detached and can fail silently.**
`clean_tee_status` is `#[private]` and runs as a separate receipt in a later block. If it fails (e.g., insufficient gas budget, or any panic), the failure is silent — the main `vote_reshared` transaction already succeeded. The stale votes remain in `tee_state.votes`, `tee_state.launcher_votes`, and `tee_state.measurement_votes` indefinitely.

```rust
// crates/contract/src/tee/tee_state.rs ~396-400
pub fn clean_non_participant_votes(&mut self, participants: &Participants) {
    self.votes = self.votes.get_remaining_votes(participants);
    self.launcher_votes = self.launcher_votes.get_remaining_votes(participants);
    self.measurement_votes = self.measurement_votes.get_remaining_votes(participants);
}
``` [2](#0-1) 

**Step 3 — TEE vote counting does not filter by current participants.**
`vote_code_hash` (and analogously `vote_add_launcher_hash`, `vote_add_os_measurement`) counts all votes stored in the map, including stale ones from removed participants:

```rust
// crates/contract/src/lib.rs ~1407-1431
pub fn vote_code_hash(&mut self, code_hash: NodeImageHash) -> Result<(), Error> {
    let participant = AuthenticatedParticipantId::new(threshold_parameters.participants())?;
    let votes = self.tee_state.vote(code_hash, &participant);  // counts ALL stored votes
    if votes >= self.threshold()?.value() {
        self.tee_state.whitelist_tee_proposal(code_hash, tee_upgrade_deadline_duration);
    }
    Ok(())
}
``` [3](#0-2) 

The `LauncherHashVotes::count_votes` (and the analogous `CodeHashesVotes`) iterates over the entire `vote_by_account` map with no participant-membership filter:

```rust
// crates/contract/src/tee/proposal.rs ~112-120
fn count_votes(&self, action: &LauncherVoteAction) -> u64 {
    u64::try_from(
        self.vote_by_account.values().filter(|a| *a == action).count(),
    ).expect("participant count should not overflow u64")
}
``` [4](#0-3) 

**Contrast with `vote_update`.**
The developers already recognized this class of bug for contract-update votes and added an explicit current-participant filter there:

```rust
// crates/contract/src/lib.rs ~1362-1374
// Filter votes to only count current participants voting for this specific update.
// This ensures correctness even if the cleanup promise in MpcContract::vote_reshared() fails.
let valid_votes_count = running_state.parameters.participants().participants()
    .iter()
    .filter(|(account_id, _, _)| {
        self.proposed_updates.vote_by_participant.get(account_id)
            .is_some_and(|voted_id| *voted_id == id)
    })
    .count();
``` [5](#0-4) 

No equivalent filter exists in `vote_code_hash`, `vote_add_launcher_hash`, or `vote_add_os_measurement`.

---

### Impact Explanation

If a malicious Docker image hash is whitelisted, nodes running that image can submit valid attestations and join the MPC network. The TEE security model — which is the primary mechanism preventing unauthorized key-share access and unauthorized signing — is then undermined. This breaks the production safety invariant that exactly `threshold` current participants must agree before a new code hash is accepted.

**Impact: Medium** — participant-state and contract execution-flow manipulation that breaks the TEE governance accounting invariant without requiring network-level DoS or operator misconfiguration. Escalation to Critical is possible if the whitelisted malicious image leaks key shares or issues unauthorized signatures.

---

### Likelihood Explanation

**Likelihood: Medium.**

The attack requires:
1. **K removed participants** (K ≥ 1) to vote for a target code hash before resharing. Since K < T_old (old threshold), this is below-threshold at the time of voting.
2. **M current participants** (M < T_new, below new threshold) to vote for the same hash after resharing, before `clean_tee_status` runs.
3. K + M ≥ T_new (combined stale + fresh votes reach the new threshold).

This is achievable when the new threshold T_new < T_old (threshold decreases after resharing), or when the cleanup promise fails. The window between resharing and cleanup is at least one block (NEAR processes detached-promise receipts in subsequent blocks), giving an attacker time to submit a `vote_code_hash` transaction. If the cleanup promise fails due to an insufficient gas budget (a configurable parameter), the window is permanent.

---

### Recommendation

Apply the same current-participant filter used in `vote_update` to all TEE governance vote-counting paths:

```rust
// In vote_code_hash, vote_add_launcher_hash, vote_add_os_measurement:
let valid_votes = self.tee_state.count_votes_for_current_participants(
    &hash_or_action,
    threshold_parameters.participants(),
);
if valid_votes >= self.threshold()?.value() { ... }
```

Alternatively, make `clean_tee_status` synchronous (inline the cleanup before the state transition completes), mirroring the fix recommended in the referenced report: perform cleanup **before** removing the entity from the authorized set.

---

### Proof of Concept

Setup: 5 participants (P1–P5), old threshold T_old = 3, resharing to {P3, P4, P5} with new threshold T_new = 2.

1. **Before resharing**: P1 and P2 call `vote_code_hash(malicious_hash)`. Vote count = 2 < 3 (old threshold). Hash is not yet whitelisted.
2. **Resharing completes**: `vote_reshared` transitions state. P1 and P2 are removed. `clean_tee_status` is spawned as a detached promise with insufficient gas and fails silently. Stale votes from P1 and P2 remain in `tee_state.votes`.
3. **After resharing**: P3 calls `vote_code_hash(malicious_hash)`. `tee_state.vote` counts all stored votes: P1 (stale) + P2 (stale) + P3 (fresh) = 3 votes. New threshold T_new = 2. Condition `3 >= 2` is true.
4. **Result**: `malicious_hash` is whitelisted. Only 1 current participant (P3) voted — below the threshold of 2. Nodes running the malicious image can now submit valid attestations and participate in the MPC signing network. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L1170-1193)
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
```

**File:** crates/contract/src/lib.rs (L1361-1374)
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
