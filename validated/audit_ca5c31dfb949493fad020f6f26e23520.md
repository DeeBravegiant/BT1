### Title
Deposits for Non-Winning `propose_update` Entries Are Permanently Frozen When Any Update Executes - (File: crates/contract/src/update.rs)

### Summary

When `do_update` executes a winning governance update, it unconditionally clears **all** competing proposal entries via `self.entries.clear()`. Because `UpdateEntry` stores no depositor account or deposit amount, the NEAR deposits attached by proposers of non-winning updates are permanently trapped in the contract with no refund path.

### Finding Description

`propose_update` is a `#[payable]` function that requires callers to attach a deposit proportional to the storage cost of the proposed update (contract WASM or config blob). The deposit is accepted and the excess is refunded, but the exact required amount is retained in the contract balance to cover storage staking. [1](#0-0) 

The `UpdateEntry` struct that is stored per proposal contains only the update payload and a `bytes_used` field — **no depositor account ID and no deposit amount**: [2](#0-1) 

When `vote_update` reaches threshold and calls `do_update`, the winning entry is removed and then **all remaining entries are cleared** with no refund: [3](#0-2) 

Specifically, lines 198–200 unconditionally wipe every competing proposal: [4](#0-3) 

Because `UpdateEntry` carries no record of who deposited or how much, there is no information available to issue refunds at clear-time. The NEAR tokens are absorbed into the contract's balance permanently.

### Impact Explanation

Every participant who proposed a competing update loses their full storage deposit — permanently. For a contract WASM update, `bytes_used` includes the full code length plus a fixed overhead for 128 participant votes: [5](#0-4) 

A 200 KB WASM blob yields a required deposit on the order of ~20 NEAR (at NEAR's storage byte cost). Multiple competing proposals can be in flight simultaneously, so the total frozen amount scales with the number of competing proposers. The funds are irrecoverable — there is no admin withdrawal, no `remove_update` function, and no refund hook anywhere in the codebase.

This breaks the production safety/accounting invariant that attached deposits are either consumed for their stated purpose or returned to the sender.

### Likelihood Explanation

The scenario requires at least two participants to independently propose different updates — a realistic governance situation (e.g., one proposes a config change while another proposes a contract upgrade). Once any update reaches threshold and executes, all other proposals are silently cleared and their deposits lost. No adversarial intent is required; the loss is a structural consequence of the design.

### Recommendation

Store the depositor's `AccountId` and the exact deposit amount inside `UpdateEntry`:

```rust
pub(crate) struct UpdateEntry {
    pub(super) update: Update,
    pub(super) bytes_used: u128,
    pub(super) proposer: AccountId,      // add
    pub(super) deposit: NearToken,       // add
}
```

In `do_update`, before calling `self.entries.clear()`, iterate over all remaining entries and schedule a `Promise::new(entry.proposer).transfer(entry.deposit)` for each one. This mirrors the refund pattern already used in `propose_update` itself and in `submit_participant_info`.

### Proof of Concept

1. Participant A calls `propose_update` with a 200 KB WASM blob, attaching ~20 NEAR deposit. Entry `id=0` is stored.
2. Participant B calls `propose_update` with a different WASM blob, attaching ~20 NEAR deposit. Entry `id=1` is stored.
3. A threshold of participants call `vote_update(id=0)`.
4. `vote_update` calls `self.proposed_updates.do_update(&id=0, gas)`.
5. Inside `do_update`: entry 0 is removed (winning update deployed), then `self.entries.clear()` removes entry 1, then `self.vote_by_participant.clear()` removes all votes.
6. Participant B's ~20 NEAR deposit is now permanently locked in the contract. No refund is issued. No recovery path exists. [3](#0-2) [6](#0-5)

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
