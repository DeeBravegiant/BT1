### Title
Proposer Deposits Permanently Frozen When `do_update` Clears All Pending Update Proposals - (File: `crates/contract/src/update.rs`)

### Summary

When `vote_update` reaches threshold and triggers `do_update`, every pending update proposal is bulk-cleared without refunding the storage deposits paid by their proposers. Because `UpdateEntry` stores neither the proposer's account ID nor the attached deposit amount, the contract has no way to issue refunds. Those deposits are permanently frozen in the contract's balance.

### Finding Description

`propose_update` requires participants to attach a deposit equal to `ProposedUpdates::required_deposit(&update)` — up to ~17 NEAR for a full contract binary — to cover on-chain storage costs. [1](#0-0) 

The deposit is sized against `bytes_used`, which includes the full contract code and an allowance for 128 participant votes: [2](#0-1) 

When `vote_update` reaches threshold it calls `do_update`, which:
1. Removes the winning entry.
2. Bulk-clears **all** remaining entries and votes.
3. Issues **no refunds** to any proposer. [3](#0-2) 

The root cause is structural: `UpdateEntry` only stores `update` and `bytes_used`, not the proposer's `AccountId` or the deposit amount, so the contract cannot reconstruct who to pay back. [4](#0-3) 

There is no `cancel_update` endpoint. `remove_update_vote` removes only the vote record, not the proposal entry, and issues no refund. [5](#0-4) 

### Impact Explanation

Every participant whose proposal is swept by `do_update` permanently loses their deposit. With the test-validated constant of 17 NEAR per contract-binary proposal, a scenario with N concurrent proposals results in N × 17 NEAR frozen in the contract's balance with no recovery path. This breaks the production accounting invariant that storage deposits are returned when storage is freed, and constitutes permanent freezing of participant funds. [6](#0-5) 

### Likelihood Explanation

In a multi-participant governance system it is routine for several participants to propose competing updates (e.g., different config values or contract binaries) before consensus is reached. The `do_update` bulk-clear is triggered on every successful threshold vote, so every governance cycle that involves more than one concurrent proposal silently destroys the non-winning proposers' deposits.

### Recommendation

Add `proposer: AccountId` and `attached_deposit: NearToken` fields to `UpdateEntry`. In `do_update`, before calling `self.entries.clear()`, iterate over the remaining entries and schedule `Promise::new(entry.proposer).transfer(entry.attached_deposit)` for each one. Also refund the winning proposer's deposit after the update is deployed (storage is freed at that point).

### Proof of Concept

1. Participant A calls `propose_update` attaching 17 NEAR → proposal `id=0` stored.
2. Participant B calls `propose_update` attaching 17 NEAR → proposal `id=1` stored.
3. Threshold participants call `vote_update(id=0)`.
4. `do_update(&id=0, gas)` executes:
   - `self.entries.remove(&id=0)` — proposal A removed.
   - `self.entries.clear()` — proposal B silently deleted, **no refund**.
   - `self.vote_by_participant.clear()`.
5. Participant B's 17 NEAR is permanently frozen in the contract balance. No function exists to recover it. [7](#0-6)

### Citations

**File:** crates/contract/src/lib.rs (L1308-1316)
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
```

**File:** crates/contract/src/lib.rs (L1397-1404)
```rust
    pub fn remove_update_vote(&mut self) {
        log!("remove_update_vote: signer={}", env::signer_account_id(),);
        let ProtocolContractState::Running(_running_state) = &self.protocol_state else {
            env::panic_str("protocol must be in running state");
        };
        let voter = self.voter_or_panic();
        self.proposed_updates.remove_vote(&voter);
    }
```

**File:** crates/contract/src/update.rs (L132-135)
```rust
pub(crate) struct UpdateEntry {
    pub(super) update: Update,
    pub(super) bytes_used: u128,
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

**File:** crates/contract/tests/sandbox/utils/consts.rs (L46-46)
```rust
pub const CURRENT_CONTRACT_DEPLOY_DEPOSIT: NearToken = NearToken::from_millinear(17000);
```
