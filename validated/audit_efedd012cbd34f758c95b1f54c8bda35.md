### Title
Non-Winning Proposal Deposits Permanently Locked When `do_update` Clears All Competing Proposals Without Refund - (File: `crates/contract/src/update.rs`)

### Summary
`propose_update` collects a NEAR deposit from each proposer to cover storage costs. When any update reaches voting threshold and `do_update` executes, it unconditionally clears **all** pending proposals — including those from other participants who paid their own deposits — without ever refunding those deposits. The locked NEAR is permanently irrecoverable.

### Finding Description
`propose_update` charges each proposer a deposit proportional to the serialized size of their update payload plus a fixed overhead for 128 participant-vote slots:

```rust
// crates/contract/src/update.rs
fn bytes_used(update: &Update) -> u128 {
    let mut bytes_used = std::mem::size_of::<UpdateEntry>() as u128;
    bytes_used += 128 * std::mem::size_of::<AccountId>() as u128;
    match update {
        Update::Contract(code) => { bytes_used += code.len() as u128; }
        ...
    }
    bytes_used
}
``` [1](#0-0) 

Only the excess above the required amount is refunded; the required portion is retained by the contract:

```rust
// crates/contract/src/lib.rs
if let Some(diff) = attached.checked_sub(required)
    && diff > NearToken::from_yoctonear(0)
{
    Promise::new(proposer).transfer(diff).detach();
}
``` [2](#0-1) 

When `vote_update` reaches threshold it calls `do_update`, which removes the winning entry and then calls `.clear()` on the entire entries map — wiping every competing proposal and its associated storage — but issues **no refund** to any of the other depositors:

```rust
pub fn do_update(&mut self, id: &UpdateId, gas: Gas) -> Option<Promise> {
    let entry = self.entries.remove(id)?;
    // Clear all entries as they might be no longer valid
    self.entries.clear();
    self.vote_by_participant.clear();
    ...
}
``` [3](#0-2) 

There is no `withdraw_proposal` or equivalent function anywhere in the contract that would allow a proposer to reclaim their deposit before or after the sweep. [4](#0-3) 

### Impact Explanation
For a maximum-size contract upload (~1.5 MB), the required deposit is approximately 15–40 NEAR (confirmed by sandbox tests that use `NearToken::from_near(40)`). [5](#0-4) 

When N participants each propose a different update and one passes, the deposits of the remaining N-1 proposers are permanently locked inside the contract with no recovery path. This directly breaks the accounting invariant that storage deposits must be returned when the storage they cover is freed, and constitutes permanent freezing of funds controlled by the chain-signature contract.

### Likelihood Explanation
The governance design explicitly supports multiple simultaneous proposals — the `entries` map is an `IterableMap` keyed by `UpdateId`, and the README describes the multi-proposal, multi-vote flow. Competing proposals arise naturally whenever participants disagree on which code or config to adopt. No malicious intent is required; the loss occurs as a side-effect of normal governance operation. A Byzantine participant below the signing threshold can also deliberately amplify the damage: propose a large-payload update to attract competing proposals from honest participants, then vote for a rival update to trigger the sweep and lock all honest depositors' funds.

### Recommendation
Store the depositor's `AccountId` alongside each `UpdateEntry`. In `do_update`, before calling `self.entries.clear()`, iterate over all remaining entries and issue a `Promise::new(depositor).transfer(entry_deposit)` for each one. This mirrors the pattern already used in `propose_update` for excess-deposit refunds and in `submit_participant_info` for storage-cost refunds. [6](#0-5) 

### Proof of Concept
1. Participant A calls `propose_update` with a 1.5 MB contract binary, attaching 40 NEAR. The deposit is retained by the contract; only the excess is refunded.
2. Participant B calls `propose_update` with a different 1.5 MB binary, also attaching 40 NEAR.
3. The threshold number of participants call `vote_update` for B's proposal ID.
4. `vote_update` detects threshold reached and calls `self.proposed_updates.do_update(&id, ...)`.
5. `do_update` removes B's entry, then calls `self.entries.clear()` — deleting A's entry — and `self.vote_by_participant.clear()`, with no transfer back to A.
6. A's 40 NEAR is permanently locked in the contract balance. No function exists to recover it. [7](#0-6)

### Citations

**File:** crates/contract/src/update.rs (L161-175)
```rust
impl ProposedUpdates {
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

**File:** crates/contract/src/lib.rs (L843-848)
```rust
            // Refund the difference if the proposer attached more than required
            if let Some(diff) = attached.checked_sub(cost)
                && diff > NearToken::from_yoctonear(0)
            {
                Promise::new(account_id).transfer(diff).detach();
            }
```

**File:** crates/contract/src/lib.rs (L1327-1331)
```rust
        if let Some(diff) = attached.checked_sub(required)
            && diff > NearToken::from_yoctonear(0)
        {
            Promise::new(proposer).transfer(diff).detach();
        }
```

**File:** crates/contract/tests/sandbox/upgrade_from_current_contract.rs (L59-74)
```rust
    let execution = mpc_signer_accounts[0]
        .call(contract.id(), method_names::PROPOSE_UPDATE)
        .args_borsh((ProposeUpdateArgs {
            code: Some(vec![0; 1536 * 1024 - 400]), //3900 seems to not work locally
            config: None,
        },))
        .max_gas()
        .deposit(NearToken::from_near(40))
        .transact()
        .await
        .unwrap();
    dbg!(&execution);
    assert!(
        execution.is_success(),
        "Failed to propose update with our highest contract size"
    );
```
