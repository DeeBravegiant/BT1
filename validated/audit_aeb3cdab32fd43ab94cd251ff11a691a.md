### Title
Propose-Update Deposits of Non-Executed Proposals Are Permanently Frozen When `do_update` Clears All Entries Without Refunding — (`File: crates/contract/src/update.rs`)

### Summary
When `vote_update` reaches threshold and `do_update` is triggered, it clears **all** pending update proposals (not just the executed one) via `self.entries.clear()`. The NEAR deposits that other proposers paid to cover storage costs for their proposals are never returned. Because no `cancel_update` or deposit-reclaim path exists anywhere in the contract, those funds are permanently locked in the contract balance.

### Finding Description
`propose_update` requires a deposit proportional to the storage consumed by the uploaded contract code or config:

```rust
// crates/contract/src/lib.rs ~1308-1316
let attached = env::attached_deposit();
let required = ProposedUpdates::required_deposit(&update);
if attached < required { ... }
```

Only the **excess** above `required` is refunded at proposal time:

```rust
// crates/contract/src/lib.rs ~1327-1331
if let Some(diff) = attached.checked_sub(required)
    && diff > NearToken::from_yoctonear(0)
{
    Promise::new(proposer).transfer(diff).detach();
}
```

The `required` portion is retained by the contract. When threshold votes are reached and `do_update` executes, it clears every other pending proposal:

```rust
// crates/contract/src/update.rs ~195-201
pub fn do_update(&mut self, id: &UpdateId, gas: Gas) -> Option<Promise> {
    let entry = self.entries.remove(id)?;
    // Clear all entries as they might be no longer valid
    self.entries.clear();
    self.vote_by_participant.clear();
    ...
}
```

The storage freed by `entries.clear()` is reclaimed into the contract's own balance, but **no transfer back to the original depositors is issued**. There is no `cancel_update`, `remove_update`, or deposit-reclaim function anywhere in the contract. The only vote-related removal is `remove_update_vote`, which only removes a participant's vote record — it does not touch the proposal entry or its associated deposit.

### Impact Explanation
Every participant who proposed a competing update loses their deposit permanently when any other proposal is executed. For a max-size contract upload (~1.5 MB), the required deposit is approximately 40 NEAR (as exercised in the sandbox test `test_propose_contract_max_size_upload`). In a network with multiple participants each proposing different contract versions, the total permanently frozen NEAR can be substantial. The freed storage stake silently accretes to the contract's balance with no accounting trail and no recovery path. This breaks the production safety/accounting invariant that depositors can recover funds for storage that is no longer in use.

### Likelihood Explanation
This is triggered in the normal governance flow. Any time multiple participants propose competing updates (a realistic scenario during contested upgrades or when participants disagree on config values) and one reaches threshold, all other proposers lose their deposits. No attacker capability is required — the loss is an inherent consequence of the existing `do_update` logic.

### Recommendation
Before calling `self.entries.clear()`, iterate over all remaining entries and issue a `Promise::new(proposer).transfer(deposit)` refund for each. The proposer account must be stored alongside the `UpdateEntry` at proposal time. Alternatively, implement a `cancel_update(id: UpdateId)` endpoint that allows a proposer to withdraw their own proposal and reclaim the deposit before it is executed.

### Proof of Concept

1. Participant A calls `propose_update` with a 1.5 MB contract blob, attaching 40 NEAR. The contract stores the entry and retains 40 NEAR.
2. Participant B calls `propose_update` with a different blob, attaching 40 NEAR. The contract stores a second entry and retains another 40 NEAR.
3. Threshold participants vote for B's proposal via `vote_update`.
4. `do_update` is triggered: it removes B's entry, then calls `self.entries.clear()` (erasing A's entry) and `self.vote_by_participant.clear()`. No refund is issued to A.
5. A's 40 NEAR is now permanently in the contract balance. There is no function A can call to recover it. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** crates/contract/src/lib.rs (L1383-1387)
```rust
        let Some(_promise) = self.proposed_updates.do_update(&id, update_gas_deposit) else {
            return Err(InvalidParameters::UpdateNotFound.into());
        };

        Ok(true)
```

**File:** crates/contract/src/update.rs (L162-174)
```rust
    pub fn required_deposit(update: &Update) -> NearToken {
        required_deposit(bytes_used(update))
    }

    /// Propose an update given the new contract code and/or config.
    pub fn propose(&mut self, update: Update) -> UpdateId {
        let bytes_used = bytes_used(&update);

        let id = self.id.generate();
        self.entries.insert(id, UpdateEntry { update, bytes_used });

        id
    }
```

**File:** crates/contract/src/update.rs (L195-227)
```rust
    pub fn do_update(&mut self, id: &UpdateId, gas: Gas) -> Option<Promise> {
        let entry = self.entries.remove(id)?;

        // Clear all entries as they might be no longer valid
        self.entries.clear();
        self.vote_by_participant.clear();

        let mut promise = Promise::new(env::current_account_id());
        match entry.update {
            Update::Contract(code) => {
                // deploy contract then do a `migrate` call to migrate state.
                promise = promise.deploy_contract(code).function_call(
                    method_names::MIGRATE,
                    Vec::new(),
                    NearToken::from_near(0),
                    gas,
                );
            }
            Update::Config(config) => {
                // If we vote for a new config, we should use
                // the value `contract_upgrade_deposit_tera_gas` from the config
                // as the new gas value
                let new_config_gas_value = Gas::from_tgas(config.contract_upgrade_deposit_tera_gas);
                promise = promise.function_call(
                    method_names::UPDATE_CONFIG,
                    serde_json::to_vec(&(&config,)).unwrap(),
                    NearToken::from_near(0),
                    new_config_gas_value,
                );
            }
        }
        Some(promise)
    }
```
