Based on the code I've read, I can trace the exact path and confirm whether this vulnerability is real.

## Key Code Evidence

**`LauncherHashVotes::count_votes` in `proposal.rs`** (lines 112–120) counts ALL votes in `vote_by_account` matching the action — it does **not** filter by current participants: [1](#0-0) 

**`LauncherHashVotes::get_remaining_votes`** (lines 128–140) is the only mechanism that filters out non-participant votes — it filters by `participants.is_participant_given_participant_id`: [2](#0-1) 

**`TeeState::clean_non_participant_votes`** (lines 396–400) is the only caller of `get_remaining_votes` for `launcher_votes`, and it is invoked from `clean_tee_status` — a separate, non-atomic call: [3](#0-2) 

**`LauncherHashVotes::vote`** (lines 94–109) inserts the new vote and immediately calls `count_votes`, which counts stale entries from removed participants that were never purged: [4](#0-3) 

---

## Analysis

The stale-vote accumulation path is **confirmed by the code**:

1. Pre-resharing, P1 and P2 vote `Remove(hash)` → stored in `launcher_votes.vote_by_account`.
2. Resharing completes, participant set shrinks from N=5 to T=3 (P3, P4, P5).
3. `clean_tee_status` / `clean_non_participant_votes` has **not** yet executed → P1 and P2's entries remain in `vote_by_account`.
4. P3 (a valid current participant) calls `vote_remove_launcher_hash(hash)`.
5. `LauncherHashVotes::vote` inserts P3's vote, then `count_votes` counts all three matching entries (P1 stale + P2 stale + P3 fresh) = **3**.
6. The threshold check `votes >= total_participants` where `total_participants` = 3 (current set size) passes.
7. `remove_launcher_image` executes, clearing all launcher votes and removing the hash.

The unanimity invariant is broken: a single current participant achieves what should require all T=3 current participants to agree.

**Limiting factor**: `AllowedLauncherImages::remove` (lines 306–318) blocks removal of the **last** launcher hash, so the attacker cannot remove all launcher images: [5](#0-4) 

This caps the worst-case impact: nodes running the removed launcher hash fail re-attestation, but the network does not fully freeze if at least one launcher hash remains. The cascading-resharing-freeze scenario described in the question is speculative and depends on all nodes running the same (removed) launcher.

---

### Title
Stale `LauncherHashVotes` from removed participants bypass unanimity requirement for launcher hash removal — (`crates/contract/src/tee/proposal.rs`, `crates/contract/src/tee/tee_state.rs`)

### Summary
After a resharing that reduces the participant set, stale votes from removed participants persist in `tee_state.launcher_votes` until `clean_tee_status` is explicitly called. Because `LauncherHashVotes::count_votes` counts all stored votes without filtering by current participants, a single new current-participant vote can combine with stale votes to satisfy the unanimity threshold, removing a launcher image hash without unanimous consent of the actual current participant set.

### Finding Description
`LauncherHashVotes::count_votes` iterates `vote_by_account.values()` and counts all entries matching the action. It has no awareness of who the current participants are. The only cleanup path is `TeeState::clean_non_participant_votes` → `LauncherHashVotes::get_remaining_votes`, which is called exclusively from `clean_tee_status`. Since `clean_tee_status` is a separate, non-atomic governance call, there is a window after every resharing during which stale votes from removed participants remain live. A single current participant can exploit this window to trigger `remove_launcher_image` with fewer than unanimous current-participant votes.

### Impact Explanation
The unanimity invariant for launcher hash removal is violated. Nodes running the removed launcher hash will fail `reverify_participants` checks, causing them to be excluded from the active participant set in subsequent `reverify_and_cleanup_participants` calls. If a significant fraction of nodes run the removed launcher, this degrades the effective signing threshold and can disrupt bridge operations. This fits **Medium** impact: it breaks a production safety/accounting invariant (unanimity for launcher removal) without directly enabling key theft or unauthorized signing.

### Likelihood Explanation
The attack window exists after every resharing that reduces the participant set, provided at least one removed participant had previously voted for the same `Remove(hash)` action. The attacker needs to be a legitimate current participant (post-resharing) and must act before `clean_tee_status` is called. This is a realistic race condition in production.

### Recommendation
In `LauncherHashVotes::vote` (and equivalently `CodeHashesVotes::vote` and `MeasurementVotes::vote`), pass the current `Participants` set and filter `vote_by_account` to only count votes from current participants before comparing against the threshold. Alternatively, call `clean_non_participant_votes` atomically at the end of every resharing completion handler, before any subsequent governance calls can be processed.

### Proof of Concept
```
// N=5: P1, P2, P3, P4, P5
// P1 and P2 vote Remove(hash) → launcher_votes has 2 entries
vote_remove_launcher_hash(hash) as P1  // count=1
vote_remove_launcher_hash(hash) as P2  // count=2

// Reshare to T=3: {P3, P4, P5}
// clean_tee_status NOT called → P1, P2 entries still in launcher_votes

// P3 votes
vote_remove_launcher_hash(hash) as P3
// count_votes returns 3 (P1 stale + P2 stale + P3 fresh)
// total_participants = 3 (current set size)
// 3 >= 3 → remove_launcher_image(hash) executes
// Launcher hash removed without P4 or P5 consent
```

### Citations

**File:** crates/contract/src/tee/proposal.rs (L94-109)
```rust
    pub fn vote(
        &mut self,
        action: LauncherVoteAction,
        participant: &AuthenticatedParticipantId,
    ) -> u64 {
        if self
            .vote_by_account
            .insert(participant.clone(), action.clone())
            .is_some()
        {
            log!("removed old launcher vote for signer");
        }
        let total = self.count_votes(&action);
        log!("total launcher votes for action: {}", total);
        total
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

**File:** crates/contract/src/tee/proposal.rs (L128-140)
```rust
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

**File:** crates/contract/src/tee/proposal.rs (L306-318)
```rust
    pub fn remove(&mut self, launcher_hash: &LauncherImageHash) -> bool {
        let would_remain = self
            .entries
            .iter()
            .filter(|e| &e.launcher_hash != launcher_hash)
            .count();
        if would_remain == 0 {
            return false;
        }
        let len_before = self.entries.len();
        self.entries.retain(|e| &e.launcher_hash != launcher_hash);
        self.entries.len() < len_before
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
