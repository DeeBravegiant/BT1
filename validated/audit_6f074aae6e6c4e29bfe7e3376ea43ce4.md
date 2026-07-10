### Title
Deposits for Non-Winning `propose_update` Proposals Are Permanently Lost When Any Update Is Executed - (File: `crates/contract/src/update.rs`)

### Summary

When a participant calls `propose_update`, they must attach a NEAR deposit to cover storage costs for the proposal. When any update reaches the voting threshold and `do_update` is called, it clears **all** pending proposals without refunding the deposits attached to the non-winning ones. Because `UpdateEntry` never records the proposer's identity or deposit amount, those funds are permanently absorbed into the contract balance with no recovery path.

### Finding Description

`propose_update` in `crates/contract/src/lib.rs` requires a deposit proportional to the size of the proposed update (WASM code or config):

```rust
let attached = env::attached_deposit();
let required = ProposedUpdates::required_deposit(&update);
// only the excess above `required` is refunded immediately
if let Some(diff) = attached.checked_sub(required) && diff > NearToken::from_yoctonear(0) {
    Promise::new(proposer).transfer(diff).detach();
}
```

The `required` portion is kept by the contract. The `UpdateEntry` stored in `proposed_updates.entries` contains only the update payload and `bytes_used` — no proposer `AccountId` and no deposit amount:

```rust
pub(crate) struct UpdateEntry {
    pub(super) update: Update,
    pub(super) bytes_used: u128,
}
```

When `do_update` is triggered by `vote_update` reaching threshold, it removes the winning entry and then unconditionally clears every other pending proposal:

```rust
pub fn do_update(&mut self, id: &UpdateId, gas: Gas) -> Option<Promise> {
    let entry = self.entries.remove(id)?;
    // Clear all entries as they might be no longer valid
    self.entries.clear();          // ← all other proposals deleted
    self.vote_by_participant.clear();
    ...
}
```

No refund is issued to the proposers of the cleared entries. Because the proposer identity and deposit amount were never stored in `UpdateEntry`, there is no information available to issue refunds at this point.

### Impact Explanation

Every participant who proposed a non-winning update permanently loses their storage deposit. For a typical MPC contract WASM (~1 MB), `bytes_used` includes the code length plus overhead for 128 assumed participant votes, making the required deposit on the order of tens to hundreds of NEAR tokens per proposal. Multiple participants can independently propose competing updates; when one reaches threshold, all others are silently cleared and their deposits are irrecoverably absorbed into the contract balance. This breaks the production accounting invariant that storage deposits are returned when the storage they cover is freed.

### Likelihood Explanation

The scenario is realistic in any governance round where participants disagree on which update to apply. Each participant is permitted to call `propose_update` independently. The loss is automatic and requires no adversarial action beyond the normal governance flow reaching threshold.

### Recommendation

Store the proposer's `AccountId` and the exact deposit amount inside `UpdateEntry`:

```rust
pub(crate) struct UpdateEntry {
    pub(super) update: Update,
    pub(super) bytes_used: u128,
    pub(super) proposer: AccountId,   // add
    pub(super) deposit: NearToken,    // add
}
```

In `do_update`, before calling `self.entries.clear()`, iterate over all remaining entries and schedule a `Promise::new(entry.proposer).transfer(entry.deposit)` for each one. This mirrors the refund pattern already used in `propose_update` for excess deposits.

### Proof of Concept

1. Participant A calls `propose_update` with a 1 MB WASM, attaching ~100 NEAR as required deposit. `UpdateEntry { update: ..., bytes_used: ... }` is stored; A's identity and deposit are discarded.
2. Participant B calls `propose_update` with a different WASM, also attaching ~100 NEAR.
3. Participants vote for A's proposal until threshold is reached; `vote_update` calls `do_update`.
4. `do_update` removes A's entry, then calls `self.entries.clear()` — B's entry is deleted.
5. B's ~100 NEAR deposit is permanently locked in the contract with no refund path.

Relevant code locations:

- Deposit taken but not stored: [1](#0-0) 
- `UpdateEntry` missing proposer/deposit fields: [2](#0-1) 
- `do_update` clears all entries without refunding: [3](#0-2) 
- `bytes_used` calculation showing deposit magnitude: [4](#0-3)

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

**File:** crates/contract/src/update.rs (L132-135)
```rust
pub(crate) struct UpdateEntry {
    pub(super) update: Update,
    pub(super) bytes_used: u128,
}
```

**File:** crates/contract/src/update.rs (L195-201)
```rust
    pub fn do_update(&mut self, id: &UpdateId, gas: Gas) -> Option<Promise> {
        let entry = self.entries.remove(id)?;

        // Clear all entries as they might be no longer valid
        self.entries.clear();
        self.vote_by_participant.clear();

```

**File:** crates/contract/src/update.rs (L278-295)
```rust
fn bytes_used(update: &Update) -> u128 {
    let mut bytes_used = std::mem::size_of::<UpdateEntry>() as u128;

    // Assume a high max of 128 participant votes per update entry.
    bytes_used += 128 * std::mem::size_of::<AccountId>() as u128;

    match update {
        Update::Contract(code) => {
            bytes_used += code.len() as u128;
        }
        Update::Config(config) => {
            let bytes = serde_json::to_vec(&config).unwrap();
            bytes_used += bytes.len() as u128;
        }
    }

    bytes_used
}
```
