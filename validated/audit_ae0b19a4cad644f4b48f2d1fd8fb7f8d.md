### Title
Stale `parameters_votes` Not Cleared on Resharing Cancellation Enables Single-Participant Governance Bypass — (File: `crates/contract/src/state/resharing.rs`)

---

### Summary

When a resharing is cancelled via `vote_cancel_resharing`, the `parameters_votes` accumulated inside `previous_running_state` during re-proposal voting are not cleared. The returned running state carries those stale votes verbatim. Because the epoch ID required for a fresh `vote_new_parameters` after cancellation is identical to the one used during the cancelled re-proposal round, a single participant can immediately trigger a new resharing by casting one vote for the same proposal — without the remaining N−1 participants providing fresh consent.

---

### Finding Description

**Root cause — stale votes survive cancellation**

During resharing, participants may call `vote_new_parameters` to re-propose a different participant set. Each such call mutates `previous_running_state.parameters_votes` directly:

```rust
// resharing.rs  vote_new_parameters
if self.previous_running_state.process_new_parameters_proposal(proposal)? {
``` [1](#0-0) 

If the re-proposal never reaches unanimous consent, those votes accumulate in `previous_running_state.parameters_votes` without being cleared.

When resharing is cancelled, `vote_cancel_resharing` simply clones `previous_running_state` and sets one extra field:

```rust
let mut previous_running_state = self.previous_running_state.clone();
previous_running_state.previously_cancelled_resharing_epoch_id = Some(prospective_epoch_id);
Some(previous_running_state)
``` [2](#0-1) 

`parameters_votes` is **never reset** here. Compare this with `RunningContractState::new`, which always initialises `parameters_votes` to `ThresholdParametersVotes::default()`: [3](#0-2) 

**Epoch-ID alignment makes stale votes immediately usable**

After cancellation, `prospective_epoch_id()` in the running state returns `cancelled_epoch_id.next()`: [4](#0-3) 

During resharing, `vote_new_parameters` required epoch `prospective_epoch_id().next()` = `resharing_epoch + 1`. After cancellation, `prospective_epoch_id()` = `cancelled_epoch_id.next()` = `resharing_epoch + 1`. The two epoch IDs are **identical**, so the stale votes are valid inputs for the very next `vote_new_parameters` call in the restored running state.

**Triggering resharing with one vote**

`process_new_parameters_proposal` counts votes for a proposal and returns `true` when `new_num_participants == n_votes` (unanimous): [5](#0-4) 

If N−1 participants voted for proposal X during the cancelled re-proposal round, their votes remain in `parameters_votes`. One additional participant voting for X after cancellation brings the count to N, immediately triggering a resharing transition — without the N−1 participants providing any fresh consent.

---

### Impact Explanation

The invariant broken is: **resharing requires unanimous fresh consent from all proposed participants at the time of the vote**. With stale votes in place, a single participant can force the contract into `Resharing` state, changing the active participant set and the epoch under which key shares are distributed. This is a participant-state and contract execution-flow manipulation that breaks a production safety/accounting invariant (governance unanimity for resharing). It maps to the **Medium** allowed impact: *"participant-state or contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration."*

---

### Likelihood Explanation

The scenario is realistic in any network where:
1. A resharing is initiated and then stalls (e.g., a node goes offline).
2. Participants attempt a re-proposal during the stalled resharing, accumulating N−1 votes for proposal X.
3. The resharing is cancelled (requires only T votes from the previous running state).
4. One participant — possibly the same one who orchestrated the cancellation — immediately calls `vote_new_parameters` with proposal X, leveraging the N−1 stale votes to trigger a resharing without the other participants' knowledge or fresh agreement.

The N−1 participants can defend by overwriting their stale votes, but only if they are aware of the issue. The attack is silent and requires no privileged access.

---

### Recommendation

Reset `parameters_votes` to `ThresholdParametersVotes::default()` inside `vote_cancel_resharing` before returning the restored running state, mirroring what `RunningContractState::new` already does:

```rust
// resharing.rs  vote_cancel_resharing
let mut previous_running_state = self.previous_running_state.clone();
previous_running_state.previously_cancelled_resharing_epoch_id = Some(prospective_epoch_id);
previous_running_state.parameters_votes = ThresholdParametersVotes::default(); // ADD THIS
Some(previous_running_state)
``` [2](#0-1) 

---

### Proof of Concept

```
State: Running, N participants, threshold T, epoch E.

1. vote_new_parameters(E+1, proposal_A) — all N participants vote → Resharing(epoch E+1).

2. During resharing, N−1 participants call vote_new_parameters(E+2, proposal_X).
   → previous_running_state.parameters_votes now holds N−1 entries for proposal_X.
   → Re-proposal does NOT complete (N−1 < N).

3. T participants call vote_cancel_resharing().
   → Contract returns to Running state.
   → previously_cancelled_resharing_epoch_id = E+1.
   → parameters_votes still holds N−1 stale entries for proposal_X.
   → prospective_epoch_id() = E+2.

4. One participant (not among the N−1 who voted in step 2) calls
   vote_new_parameters(E+2, proposal_X).
   → process_new_parameters_proposal counts N−1 (stale) + 1 (new) = N votes.
   → N == new_num_participants → resharing triggered immediately.

Result: Resharing with proposal_X is forced without N−1 participants providing
        fresh consent, bypassing the unanimity requirement.
```

### Citations

**File:** crates/contract/src/state/resharing.rs (L69-72)
```rust
        if self
            .previous_running_state
            .process_new_parameters_proposal(proposal)?
        {
```

**File:** crates/contract/src/state/resharing.rs (L184-190)
```rust
        let running_state = if threshold_cancellation_votes_reached {
            let mut previous_running_state = self.previous_running_state.clone();
            let prospective_epoch_id = self.prospective_epoch_id();
            previous_running_state.previously_cancelled_resharing_epoch_id =
                Some(prospective_epoch_id);

            Some(previous_running_state)
```

**File:** crates/contract/src/state/running.rs (L56-63)
```rust
        RunningContractState {
            domains,
            keyset,
            parameters,
            parameters_votes: ThresholdParametersVotes::default(),
            add_domains_votes: remaining_add_domain_votes,
            previously_cancelled_resharing_epoch_id: None,
        }
```

**File:** crates/contract/src/state/running.rs (L127-138)
```rust
    pub fn prospective_epoch_id(&self) -> EpochId {
        match self.previously_cancelled_resharing_epoch_id {
            // If `cancelled_epoch_id`, then a resharing has already
            // been attempted but was cancelled.
            // We must make sure to not reuse previously used prospective epoch ids,
            // and continue from the last prospective epoch id for the previous resharing attempt.
            Some(cancelled_epoch_id) => cancelled_epoch_id,
            // No resharing has been attempted for this running state.
            None => self.keyset.epoch_id,
        }
        .next()
    }
```

**File:** crates/contract/src/state/running.rs (L205-208)
```rust
        // finally, vote.
        let n_votes = self.parameters_votes.vote(proposal, candidate);
        Ok(new_num_participants == n_votes)
    }
```
