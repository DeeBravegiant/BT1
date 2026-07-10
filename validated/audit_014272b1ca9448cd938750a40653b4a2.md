### Title
Proposer Deposit Permanently Lost When Competing Update Proposals Are Cleared by `do_update` - (File: crates/contract/src/update.rs)

### Summary
When a contract update reaches threshold and `do_update` executes, it unconditionally clears **all** pending update entries via `self.entries.clear()`. Proposers of non-winning proposals paid a storage-staking deposit (up to ~17 NEAR for a contract binary) that is never refunded when their entries are silently wiped. The freed storage accrues to the contract account, permanently stranding the proposers' funds.

### Finding Description
`propose_update` in `crates/contract/src/lib.rs` requires an attached deposit proportional to the size of the proposed update:

```rust
let attached = env::attached_deposit();
let required = ProposedUpdates::required_deposit(&update);
// only the excess above `required` is refunded
if let Some(diff) = attached.checked_sub(required) && diff > NearToken::from_yoctonear(0) {
    Promise::new(proposer).transfer(diff).detach();
}
```

The `required` portion stays in the contract to cover storage staking for the `UpdateEntry` stored in `self.entries`. For a contract binary update this is `CURRENT_CONTRACT_DEPLOY_DEPOSIT = 17 NEAR`.

When any one proposal reaches threshold, `do_update` in `crates/contract/src/update.rs` executes:

```rust
pub fn do_update(&mut self, id: &UpdateId, gas: Gas) -> Option<Promise> {
    let entry = self.entries.remove(id)?;
    // Clear all entries as they might be no longer valid
    self.entries.clear();
    self.vote_by_participant.clear();
    ...
}
```

`self.entries.clear()` removes every competing proposal from storage. The storage staking for those entries is released back to the **contract account**, not to the original proposers. There is no code path that schedules a `Promise::transfer` back to the proposers of the cleared entries. The `remove_vote` helper only removes from `vote_by_participant`, not from `entries`, so there is also no way for a proposer to voluntarily withdraw their proposal and recover their deposit before a competing update executes.

### Impact Explanation
Every participant who proposed a competing update loses their full storage-staking deposit (up to ~17 NEAR per proposal) permanently. The contract account silently absorbs those funds. This breaks the NEAR storage-staking accounting invariant: deposits paid to cover storage must be returned when that storage is freed. The impact is a direct, permanent loss of funds from participants, classified as a Medium balance/accounting invariant violation.

### Likelihood Explanation
The scenario is triggered by ordinary governance activity. Multiple participants routinely propose competing updates (e.g., different code versions or config values). Whenever one proposal reaches threshold, all others are cleared without refund. No malicious action is required; the loss is a structural consequence of normal contract operation.

### Recommendation
Before calling `self.entries.clear()`, iterate over all remaining entries and schedule a `Promise::transfer` back to each proposer for the deposit they paid. The `UpdateEntry` struct should be extended to record the proposer's `AccountId` and the exact deposit amount at proposal time, mirroring the pattern used in `PendingAttestation` (which stores `attached_deposit` and refunds it on failure).

### Proof of Concept

1. Participant A calls `propose_update` with a 1 MB contract binary, attaching ~17 NEAR deposit. Entry `id=0` is stored.
2. Participant B calls `propose_update` with a different binary, attaching ~17 NEAR deposit. Entry `id=1` is stored.
3. Threshold participants vote for `id=1`. `vote_update` calls `do_update(&id=1, ...)`.
4. Inside `do_update`:
   - `self.entries.remove(&id=1)` removes B's entry (used for deployment).
   - `self.entries.clear()` removes A's entry — freeing its storage, which accrues to the contract account.
   - No `Promise::transfer` is scheduled for A.
5. Participant A's ~17 NEAR is permanently locked in the contract with no recovery path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** crates/contract/src/update.rs (L195-200)
```rust
    pub fn do_update(&mut self, id: &UpdateId, gas: Gas) -> Option<Promise> {
        let entry = self.entries.remove(id)?;

        // Clear all entries as they might be no longer valid
        self.entries.clear();
        self.vote_by_participant.clear();
```

**File:** crates/contract/src/update.rs (L229-232)
```rust
    /// Removes the vote for [`AccountId`].
    pub fn remove_vote(&mut self, voter: &AccountId) {
        self.vote_by_participant.remove(voter);
    }
```

**File:** crates/contract/tests/sandbox/utils/consts.rs (L46-46)
```rust
pub const CURRENT_CONTRACT_DEPLOY_DEPOSIT: NearToken = NearToken::from_millinear(17000);
```
