### Title
`propose_update` Storage Deposits Permanently Locked When `do_update` Clears All Proposals - (File: `crates/contract/src/update.rs`)

### Summary

When `propose_update` is called, a participant attaches a NEAR storage-staking deposit proportional to the size of the proposed contract binary or config. When any update reaches threshold and `do_update` executes, it bulk-clears **all** pending proposals and their storage. The freed storage returns to the contract's own balance, but the original deposits from every proposer whose proposal was cleared are never refunded. Those NEAR tokens are permanently locked inside the MPC contract with no recovery path.

### Finding Description

`propose_update` in `crates/contract/src/lib.rs` collects a deposit from the proposer:

```rust
let attached = env::attached_deposit();
let required = ProposedUpdates::required_deposit(&update);
// ...
// Refund the difference if the proposer attached more than required.
if let Some(diff) = attached.checked_sub(required) && diff > NearToken::from_yoctonear(0) {
    Promise::new(proposer).transfer(diff).detach();
}
```

Only the **excess** above `required` is refunded. The `required` portion stays in the contract to cover storage staking for the stored proposal entry.

When `vote_update` reaches threshold it calls `do_update`, which in `crates/contract/src/update.rs` does:

```rust
pub fn do_update(&mut self, id: &UpdateId, gas: Gas) -> Option<Promise> {
    let entry = self.entries.remove(id)?;
    // Clear all entries as they might be no longer valid
    self.entries.clear();
    self.vote_by_participant.clear();
    // ...
}
```

`self.entries.clear()` removes every pending proposal — including those submitted by other participants who each paid their own `required_deposit`. The storage is freed (returning to the contract's balance), but **no refund is issued to any of those proposers**. Their NEAR is absorbed into the contract account permanently.

`required_deposit` is non-trivial: for a 1 MB contract binary it is `bytes_used * storage_byte_cost`, where `bytes_used` includes the code length plus a 128-participant-vote overhead:

```rust
fn bytes_used(update: &Update) -> u128 {
    let mut bytes_used = std::mem::size_of::<UpdateEntry>() as u128;
    bytes_used += 128 * std::mem::size_of::<AccountId>() as u128;
    match update {
        Update::Contract(code) => { bytes_used += code.len() as u128; }
        // ...
    }
    bytes_used
}
```

At NEAR's current storage cost (~1 NEAR per 10 KB), a 1 MB WASM binary requires roughly 100 NEAR in deposit. Every participant who proposed a competing update loses that deposit when any other update executes.

### Impact Explanation

Every participant who calls `propose_update` and whose proposal is subsequently cleared by a competing update executing loses their full storage deposit permanently. The MPC contract has no withdrawal function, no admin recovery path, and no mechanism to return these deposits. The funds accumulate in the contract account and are irrecoverable under the current code. This breaks the production accounting invariant that deposits paid for temporary storage should be returned when that storage is freed.

### Likelihood Explanation

This occurs in normal protocol operation whenever more than one participant proposes an update before threshold is reached. The README explicitly notes that multiple proposals can coexist simultaneously. In a network with N participants, it is routine for several to propose competing updates (e.g., different config values or different WASM binaries). Every such scenario causes the non-winning proposers to lose their deposits. No adversarial action is required — ordinary governance activity triggers the loss.

### Recommendation

Track the proposer's `AccountId` and the `required` deposit amount inside `UpdateEntry`. In `do_update`, before calling `self.entries.clear()`, iterate over all remaining entries and issue a `Promise::new(entry.proposer).transfer(entry.deposit)` refund for each one. This mirrors the pattern already used in `submit_participant_info` and `propose_update` itself for excess-deposit refunds.

### Proof of Concept

1. Participant A calls `propose_update` with a 1 MB WASM, attaching ~100 NEAR. The `required` portion (~100 NEAR) is retained by the contract.
2. Participant B calls `propose_update` with a different WASM, also attaching ~100 NEAR.
3. Threshold participants vote for B's proposal via `vote_update`.
4. `do_update` executes: removes B's entry, then calls `self.entries.clear()` which removes A's entry. A's ~100 NEAR deposit is never returned.
5. A's NEAR is permanently locked in the contract. No function exists to recover it.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** crates/contract/src/update.rs (L278-299)
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

fn required_deposit(bytes_used: u128) -> NearToken {
    env::storage_byte_cost().saturating_mul(bytes_used)
}
```
