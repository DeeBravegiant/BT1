### Title
Stale Votes from Removed Participants Count Toward Governance Threshold in `CodeHashesVotes`, `LauncherHashVotes`, and `MeasurementVotes` - (File: `crates/contract/src/tee/proposal.rs`, `crates/contract/src/tee/measurements.rs`)

---

### Summary

Three TEE governance vote trackers — `CodeHashesVotes`, `LauncherHashVotes`, and `MeasurementVotes` — count **all stored votes** when checking whether a proposal crosses the signing threshold, including votes from participants who have since been removed via resharing. This is inconsistent with `TeeVerifierVotes`, which correctly filters by the current participant set at vote time. As a result, a participant who is removed from the MPC network can have their old vote persist and contribute to crossing the governance threshold, allowing a TEE code hash, launcher hash, or OS measurement set to be whitelisted with fewer current-participant approvals than the threshold requires.

---

### Finding Description

The `CodeHashesVotes.count_votes()` function in `crates/contract/src/tee/proposal.rs` counts every stored vote for a proposal without filtering by the current participant set:

```rust
fn count_votes(&self, proposal: &NodeImageHash) -> u64 {
    self.proposal_by_account
        .values()
        .filter(|&prop| prop == proposal)
        .count() as u64
}
```

The same pattern appears in `LauncherHashVotes.count_votes()` and `MeasurementVotes.count_votes()` in the same files.

By contrast, `TeeVerifierVotes.vote()` in `crates/contract/src/tee/verifier_votes.rs` correctly filters at vote time:

```rust
let count_usize = {
    let voter_set = self.pending.vote(participant, proposal_hash);
    voter_set.count_for(|p| participants.is_participant_given_participant_id(&p.get()))
};
```

All three affected types expose a `get_remaining_votes()` method intended to prune stale votes during state transitions, but this cleanup is not performed atomically at vote-counting time. Between a resharing that removes a participant and the next explicit `get_remaining_votes()` call, the removed participant's vote remains in `proposal_by_account` and is counted by `count_votes()`.

**Attack sequence:**
1. Participant A (who will be removed) votes for a target `NodeImageHash` / `LauncherImageHash` / `ContractExpectedMeasurements`.
2. A resharing completes, removing Participant A from the active set.
3. If `get_remaining_votes()` is not called for the affected vote store after resharing, A's vote persists.
4. Participant B (still active) votes for the same proposal.
5. `count_votes()` returns 2 (A's stale vote + B's current vote), crossing a threshold of 2 even though only 1 current participant voted.
6. The proposal is accepted and the code hash / launcher hash / measurement set is whitelisted.

---

### Impact Explanation

Whitelisting a TEE code hash, launcher image, or OS measurement set with fewer current-participant approvals than the governance threshold requires breaks the production safety invariant that governs which node software is trusted to participate in threshold signing. A malicious or compromised code hash that is incorrectly whitelisted could allow unauthorized nodes to obtain valid attestations, be admitted as participants, and ultimately participate in threshold signing rounds — potentially enabling unauthorized signature issuance below the intended security threshold.

This matches the allowed Medium impact: **participant-state manipulation that breaks production safety/accounting invariants**.

---

### Likelihood Explanation

The preconditions are realistic in normal protocol operation: participant set changes via resharing are a standard governance action. Any participant who is scheduled for removal has a window to cast a vote for a target proposal before their removal takes effect. The stale vote then persists until `get_remaining_votes()` is explicitly called. The inconsistency with `TeeVerifierVotes` (which filters correctly) confirms this is an unintentional omission rather than a design choice.

---

### Recommendation

Apply the same participant-filtering pattern used in `TeeVerifierVotes.vote()` to `CodeHashesVotes.count_votes()`, `LauncherHashVotes.count_votes()`, and `MeasurementVotes.count_votes()`. Each `count_votes` call should receive the current `ThresholdParameters` or `Participants` and filter out votes from accounts no longer in the active participant set, rather than relying solely on periodic `get_remaining_votes()` cleanup at state transitions.

---

### Proof of Concept

**Inconsistency — `CodeHashesVotes` does not filter (vulnerable):** [1](#0-0) 

**Correct pattern — `TeeVerifierVotes` filters by current participants at vote time:** [2](#0-1) 

**`get_remaining_votes` exists but is only a periodic cleanup, not an atomic guard:** [3](#0-2) 

**Same unfiltered pattern in `MeasurementVotes`:** [4](#0-3) 

**Same unfiltered pattern in `LauncherHashVotes`:** [5](#0-4)

### Citations

**File:** crates/contract/src/tee/proposal.rs (L47-52)
```rust
    fn count_votes(&self, proposal: &NodeImageHash) -> u64 {
        self.proposal_by_account
            .values()
            .filter(|&prop| prop == proposal)
            .count() as u64
    }
```

**File:** crates/contract/src/tee/proposal.rs (L59-72)
```rust
    /// Returns a new `CodeHashesVotes` containing only votes from current participants.
    pub fn get_remaining_votes(&self, participants: &Participants) -> Self {
        let remaining = self
            .proposal_by_account
            .iter()
            .filter(|(participant_id, _)| {
                participants.is_participant_given_participant_id(&participant_id.get())
            })
            .map(|(participant_id, vote)| (participant_id.clone(), *vote))
            .collect();
        CodeHashesVotes {
            proposal_by_account: remaining,
        }
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

**File:** crates/contract/src/tee/verifier_votes.rs (L74-77)
```rust
        let count_usize = {
            let voter_set = self.pending.vote(participant, proposal_hash);
            voter_set.count_for(|p| participants.is_participant_given_participant_id(&p.get()))
        };
```

**File:** crates/contract/src/tee/measurements.rs (L58-66)
```rust
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
