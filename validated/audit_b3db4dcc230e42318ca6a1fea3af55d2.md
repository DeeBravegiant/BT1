Looking at the exact code paths:

### Title
Permanent Locking of NEAR Deposits and Unbounded `entries` Map Growth via `propose_update` During Resharing — (`crates/contract/src/update.rs`, `crates/contract/src/lib.rs`)

---

### Summary

A participant from the previous running epoch can call `propose_update` while the contract is in `Resharing` state, paying a non-refundable deposit. After resharing completes and that participant is excluded from the new set, `remove_non_participant_update_votes` (spawned as a detached promise from `vote_reshared`) removes only the caller's entry from `vote_by_participant` — it never touches the `entries` map. The proposal entry and its associated deposit are permanently orphaned with no cleanup path.

---

### Finding Description

**Entrypoint — `propose_update` is callable during Resharing:**

`propose_update` gates access through `voter_or_panic()` → `voter_account()` → `authenticate_update_vote()`. [1](#0-0) 

During `Resharing`, `authenticate_update_vote` authenticates against `state.previous_running_state.parameters.participants()` — the *old* epoch's participant set. Any participant from the previous epoch can therefore call `propose_update` while resharing is in progress.

**Deposit is consumed, not tracked per-proposer:** [2](#0-1) 

The required deposit stays in the contract. Only the *excess* is refunded. There is no per-proposer deposit ledger; the contract has no way to associate a deposit with a specific proposer for later refund.

**`propose` inserts into `entries` only — no vote is recorded for the proposer:** [3](#0-2) 

The proposer has no entry in `vote_by_participant` unless they separately call `vote_update`.

**`remove_non_participant_update_votes` only cleans `vote_by_participant`, never `entries`:** [4](#0-3) [5](#0-4) 

After resharing, the detached cleanup promise calls `remove_non_participant_votes`, which iterates `vote_by_participant` and removes non-participant voters. The `entries` map is never touched. The orphaned `UpdateEntry` (which may contain an entire contract binary) persists indefinitely.

**No other cleanup path exists:**

- `remove_update_vote` only removes a vote and requires the caller to pass `voter_or_panic()` — which fails for a non-participant.
- `do_update` clears all entries only when a threshold-approved update is executed.
- There is no `remove_proposal` or deposit-refund function.

**Accumulation across resharing cycles:**

Each resharing that excludes a proposer leaves one or more `UpdateEntry` values permanently in storage. Over multiple resharing epochs, the `entries` map grows without bound.

---

### Impact Explanation

1. **Permanent deposit lock**: The deposit paid by the proposer (calculated as `env::storage_byte_cost() × bytes_used`, which for a full contract binary can be on the order of several NEAR) is irrecoverable. The contract holds it with no mechanism to return it.
2. **Unbounded storage growth**: Orphaned `UpdateEntry` values accumulate in `entries` across resharing cycles, consuming on-chain storage paid for by the contract account.
3. **Accounting invariant broken**: The invariant "deposits paid for proposals are refundable when the proposer loses participant status" is violated.

---

### Likelihood Explanation

Resharing to change the participant set is a normal operational event. A participant who proposes an update (a routine governance action) and is subsequently excluded from the new set — whether by voluntary rotation, TEE attestation failure triggering `verify_tee`, or a governance vote — will have their deposit permanently locked. No adversarial coordination is required; this occurs in the normal protocol lifecycle.

---

### Recommendation

1. **Track deposits per proposal**: Store the proposer's `AccountId` and the exact deposit amount in `UpdateEntry`.
2. **Refund on entry removal**: When `remove_non_participant_votes` (or any future cleanup) removes an entry from `entries`, transfer the stored deposit back to the original proposer.
3. **Add a `remove_proposal` method**: Allow the original proposer (or any participant post-resharing) to explicitly remove an orphaned entry and trigger a refund to the original proposer's account.
4. **Alternatively, clear entries alongside votes**: In `remove_non_participant_votes`, also remove `entries` that have no remaining participant votes and refund the associated deposit.

---

### Proof of Concept

```
1. Contract is in Running state with participants {A, B, C}.
2. Participant A calls propose_update (during Running or at the start of Resharing)
   with a contract binary; pays deposit D. Entry ID=0 is inserted into entries.
3. Resharing begins (vote_new_parameters accepted), new participant set = {B, C}.
4. Resharing completes (vote_reshared threshold reached); contract transitions to Running{B,C}.
5. Detached promise remove_non_participant_update_votes fires:
   - vote_by_participant has no entry for A (A never called vote_update), so nothing is removed.
   - entries still contains ID=0 with A's proposal.
6. Assert: proposed_updates() still shows entry ID=0.
7. Assert: A cannot call remove_update_vote (voter_or_panic fails — A is not a participant).
8. Assert: No refund of D has been issued to A.
9. Repeat steps 2–8 for a second resharing cycle: entries now contains ID=0 and ID=1.
   Storage and locked deposits grow unboundedly.
``` [6](#0-5) [7](#0-6)

### Citations

**File:** crates/contract/src/state.rs (L232-235)
```rust
            ProtocolContractState::Resharing(state) => {
                AuthenticatedParticipantId::new(
                    state.previous_running_state.parameters.participants(),
                )?;
```

**File:** crates/contract/src/lib.rs (L1170-1184)
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
```

**File:** crates/contract/src/lib.rs (L1308-1331)
```rust
        let attached = env::attached_deposit();
        let required = ProposedUpdates::required_deposit(&update);
        if attached < required {
            return Err(InvalidParameters::InsufficientDeposit {
                attached: attached.as_yoctonear(),
                required: required.as_yoctonear(),
            }
            .into());
        }

        let id = self.proposed_updates.propose(update);

        log!(
            "propose_update: signer={}, id={:?}",
            env::signer_account_id(),
            id,
        );

        // Refund the difference if the proposer attached more than required.
        if let Some(diff) = attached.checked_sub(required)
            && diff > NearToken::from_yoctonear(0)
        {
            Promise::new(proposer).transfer(diff).detach();
        }
```

**File:** crates/contract/src/lib.rs (L1798-1800)
```rust
        self.proposed_updates
            .remove_non_participant_votes(participants);
        Ok(())
```

**File:** crates/contract/src/update.rs (L167-174)
```rust
    pub fn propose(&mut self, update: Update) -> UpdateId {
        let bytes_used = bytes_used(&update);

        let id = self.id.generate();
        self.entries.insert(id, UpdateEntry { update, bytes_used });

        id
    }
```

**File:** crates/contract/src/update.rs (L229-253)
```rust
    /// Removes the vote for [`AccountId`].
    pub fn remove_vote(&mut self, voter: &AccountId) {
        self.vote_by_participant.remove(voter);
    }

    /// Removes votes from the specified accounts.
    pub fn remove_votes(&mut self, accounts_to_remove: &[AccountId]) {
        accounts_to_remove
            .iter()
            .for_each(|account| self.remove_vote(account));
    }

    /// Removes votes from accounts that are not participants.
    pub fn remove_non_participant_votes(&mut self, participants: &Participants) {
        // Note: This operation has quadratic time complexity.
        // TODO(#1572): optimize quadratic time complexity
        let non_participants: Vec<AccountId> = self
            .vote_by_participant
            .keys()
            .filter(|voter| !participants.is_participant_given_account_id(voter))
            .cloned()
            .collect();

        self.remove_votes(&non_participants);
    }
```
