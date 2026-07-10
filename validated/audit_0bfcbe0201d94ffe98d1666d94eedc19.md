Based on my analysis of the code, this is a **real vulnerability**. Here is the full finding:

---

### Title
Stale Pre-Resharing Votes in `CodeHashesVotes::count_votes` Allow Below-Threshold Whitelisting of Malicious MPC Docker Image Hash — (`crates/contract/src/tee/proposal.rs`)

---

### Summary

`CodeHashesVotes::count_votes` counts **all** stored votes unconditionally, with no filter for whether the voter is still a current participant. After a resharing that shrinks the participant set, stale votes from dropped participants remain in `proposal_by_account` until `clean_non_participant_votes` is explicitly called. A single current participant voting for a malicious hash in that window can push the raw count over threshold, triggering `whitelist_tee_proposal` with fewer than threshold current-participant votes.

---

### Finding Description

**Root cause — `CodeHashesVotes::count_votes` (no participant filter):** [1](#0-0) 

```rust
fn count_votes(&self, proposal: &NodeImageHash) -> u64 {
    self.proposal_by_account
        .values()
        .filter(|&prop| prop == proposal)
        .count() as u64
}
```

This iterates over every entry in `proposal_by_account` and counts matching hashes. There is **no call to `is_participant_given_participant_id`** or any equivalent filter. Dropped participants' votes are counted identically to current participants' votes.

**Contrast with `TeeVerifierVotes::vote` (correct participant filter):** [2](#0-1) 

```rust
let count_usize = {
    let voter_set = self.pending.vote(participant, proposal_hash);
    voter_set.count_for(|p| participants.is_participant_given_participant_id(&p.get()))
};
```

`TeeVerifierVotes` correctly passes `ThresholdParameters` into the vote call and filters the voter set by `is_participant_given_participant_id` before comparing against threshold. `CodeHashesVotes` has no equivalent guard.

**The cleanup is deferred and not atomic with resharing:** [3](#0-2) 

```rust
pub fn clean_non_participant_votes(&mut self, participants: &Participants) {
    self.votes = self.votes.get_remaining_votes(participants);
    ...
}
```

`clean_non_participant_votes` (exposed as the `clean_tee_status` contract method) is a **separate, explicitly-called transaction**. It is not invoked atomically inside the resharing completion path. This creates a window — potentially spanning many blocks — during which stale votes from dropped participants remain in `CodeHashesVotes::proposal_by_account`.

**`get_remaining_votes` exists but is only called on cleanup:** [4](#0-3) 

The filter logic is already implemented in `get_remaining_votes`, but it is only applied when `clean_non_participant_votes` runs, not at vote-counting time.

**`TeeState::vote` returns the raw unfiltered count:** [5](#0-4) 

```rust
pub fn vote(&mut self, code_hash: NodeImageHash, participant: &AuthenticatedParticipantId) -> u64 {
    self.votes.vote(code_hash, participant)
}
```

The returned `u64` is the raw count from `count_votes`. The threshold comparison in `lib.rs`'s `vote_code_hash` uses this raw count directly, so stale votes inflate it.

---

### Impact Explanation

A malicious MPC docker image hash is added to `AllowedDockerImageHashes` via `whitelist_tee_proposal`. Any node running that image subsequently passes `add_participant` / `reverify_participants` attestation checks and is admitted as a valid TEE participant. This breaks the attestation allowlist invariant: the whitelist is supposed to require threshold current-participant agreement, but it can be crossed with as few as 1 current-participant vote when stale votes from dropped participants are present.

---

### Likelihood Explanation

The attack requires:
1. Two Byzantine participants (below threshold) to vote for a malicious hash before a resharing.
2. A resharing that drops those two participants.
3. One additional participant (Byzantine or deceived) to vote for the same hash before `clean_tee_status` is called.

Steps 1–2 are within the capability of below-threshold Byzantine participants. Step 3 requires either a third Byzantine participant or a timing window before cleanup. The window is realistic: `clean_tee_status` is a separate permissioned call and may not be invoked immediately after resharing.

---

### Recommendation

Apply the same participant-filter pattern used in `TeeVerifierVotes::vote` to `CodeHashesVotes::count_votes`. The simplest fix is to pass `ThresholdParameters` (or just `&Participants`) into `CodeHashesVotes::vote` and filter `proposal_by_account` by `is_participant_given_participant_id` before counting, mirroring: [2](#0-1) 

Alternatively, call `get_remaining_votes` eagerly inside `vote` before counting, so stale entries are never included in the threshold comparison regardless of when `clean_non_participant_votes` runs.

---

### Proof of Concept

Deterministic unit test sketch (N=5, T=3):

1. Create participants `{P1, P2, P3, P4, P5}` with threshold 3.
2. P1 and P2 call `CodeHashesVotes::vote(malicious_hash, &p1)` and `vote(malicious_hash, &p2)`. Count returns 1, then 2 — below threshold.
3. Simulate resharing: new participant set is `{P3, P4, P5}`, threshold still 3. **Do not call `clean_non_participant_votes`.**
4. P3 calls `CodeHashesVotes::vote(malicious_hash, &p3)`.
5. `count_votes` iterates `proposal_by_account` which still contains P1, P2, P3 → returns **3**.
6. In `lib.rs`, `3 >= threshold(3)` → `whitelist_tee_proposal` fires.
7. Assert `allowed_docker_image_hashes` now contains `malicious_hash` even though only **1 current participant** voted for it. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/contract/src/tee/proposal.rs (L29-52)
```rust
    pub fn vote(
        &mut self,
        proposal: NodeImageHash,
        participant: &AuthenticatedParticipantId,
    ) -> u64 {
        if self
            .proposal_by_account
            .insert(participant.clone(), proposal)
            .is_some()
        {
            log!("removed old vote for signer");
        }
        let total = self.count_votes(&proposal);
        log!("total votes for proposal: {}", total);
        total
    }

    /// Counts the total number of participants who have voted for the given code hash.
    fn count_votes(&self, proposal: &NodeImageHash) -> u64 {
        self.proposal_by_account
            .values()
            .filter(|&prop| prop == proposal)
            .count() as u64
    }
```

**File:** crates/contract/src/tee/proposal.rs (L60-72)
```rust
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

**File:** crates/contract/src/tee/verifier_votes.rs (L74-77)
```rust
        let count_usize = {
            let voter_set = self.pending.vote(participant, proposal_hash);
            voter_set.count_for(|p| participants.is_participant_given_participant_id(&p.get()))
        };
```

**File:** crates/contract/src/tee/tee_state.rs (L279-285)
```rust
    pub fn vote(
        &mut self,
        code_hash: NodeImageHash,
        participant: &AuthenticatedParticipantId,
    ) -> u64 {
        self.votes.vote(code_hash, participant)
    }
```

**File:** crates/contract/src/tee/tee_state.rs (L305-316)
```rust
    pub fn whitelist_tee_proposal(
        &mut self,
        tee_proposal: NodeImageHash,
        tee_upgrade_deadline_duration: Duration,
    ) {
        self.votes.clear_votes();
        // Add compose hashes for the new MPC image across all allowed launcher images
        self.allowed_launcher_images
            .add_mpc_image_compose_hashes(&tee_proposal);
        self.allowed_docker_image_hashes
            .insert(tee_proposal, tee_upgrade_deadline_duration);
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
