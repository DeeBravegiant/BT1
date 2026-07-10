### Title
Deposits for Non-Executed Update Proposals Are Permanently Stuck When Any Update Is Executed - (`crates/contract/src/update.rs`)

### Summary

When `do_update` executes a winning governance update, it unconditionally clears **all** pending update entries via `self.entries.clear()`. Because `UpdateEntry` does not record the proposer's account ID or the deposited amount, and no refund logic exists in the clear path, every deposit paid by proposers of non-executed updates is permanently locked in the contract with no recovery mechanism.

### Finding Description

`propose_update` in `crates/contract/src/lib.rs` requires a deposit proportional to the storage cost of the proposed update payload: [1](#0-0) 

The deposit is computed from `ProposedUpdates::required_deposit`, which multiplies `env::storage_byte_cost()` by the serialized size of the update (contract WASM or config). For a large WASM binary this can be tens of NEAR.

The deposit is accepted and the update is stored, but **the proposer's account ID and the deposited amount are never recorded inside `UpdateEntry`**: [2](#0-1) 

Only `update` (the payload) and `bytes_used` (a size hint) are stored — no `proposer: AccountId`, no `deposit: NearToken`.

When a different update reaches threshold and `vote_update` calls `do_update`, the function removes the winning entry and then **bulk-clears every remaining entry and every vote** with no refund: [3](#0-2) 

Because the proposer identity and deposit amount were never persisted, the contract has no information with which to issue refunds. The NEAR tokens paid by all non-winning proposers are permanently trapped.

There is no `withdraw_proposal` or equivalent endpoint. `remove_update_vote` only removes a vote record, not the proposal entry or its deposit.

### Impact Explanation

Every participant who proposed an update that was not selected loses their full deposit — potentially tens of NEAR per proposal — with no recovery path. This is a direct, permanent loss of funds controlled by the MPC contract, matching the **Medium** impact category: *balance/accounting invariant broken without relying on network-level DoS or operator misconfiguration*.

### Likelihood Explanation

Concurrent competing proposals are a normal governance scenario: one participant proposes a contract upgrade while another proposes a config change. As soon as either reaches threshold, the other's deposit is silently forfeited. No adversarial setup is required; the loss occurs in ordinary multi-participant operation.

### Recommendation

Persist the proposer's `AccountId` and the exact deposited `NearToken` inside `UpdateEntry`:

```rust
pub(crate) struct UpdateEntry {
    pub(super) update: Update,
    pub(super) bytes_used: u128,
    pub(super) proposer: AccountId,   // add
    pub(super) deposit: NearToken,    // add
}
```

In `do_update`, before calling `self.entries.clear()`, iterate over all remaining entries and schedule a `Promise::new(entry.proposer).transfer(entry.deposit)` for each one. This mirrors the pattern already used in `propose_update` for excess-deposit refunds. [4](#0-3) 

### Proof of Concept

1. Participant A calls `propose_update` with a 500 KB WASM blob, attaching ~5 NEAR deposit. Entry `id=0` is stored.
2. Participant B calls `propose_update` with a config change, attaching ~0.1 NEAR deposit. Entry `id=1` is stored.
3. Enough participants call `vote_update(0)` to reach threshold.
4. `do_update(&0, gas)` executes: removes entry `0`, then calls `self.entries.clear()` (silently drops entry `1`) and `self.vote_by_participant.clear()`.
5. Participant B's ~0.1 NEAR is permanently locked in the contract. Participant A's ~5 NEAR is also locked if a third proposal had been made by a third participant and A's proposal was the one cleared.
6. No contract endpoint exists to recover these funds. [5](#0-4)

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

**File:** crates/contract/src/lib.rs (L1326-1331)
```rust
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
