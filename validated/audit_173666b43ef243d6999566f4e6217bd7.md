### Title
Deposits for Non-Winning `propose_update` Proposals Are Permanently Locked — (`File: crates/contract/src/update.rs`)

### Summary
When `do_update` executes a winning contract/config proposal, it unconditionally clears **all** pending proposals via `self.entries.clear()`. The NEAR deposits paid by proposers of the non-winning entries are never refunded and are permanently locked in the contract, because `UpdateEntry` stores no proposer identity or deposit amount.

### Finding Description

`propose_update` requires each proposer to attach a deposit proportional to the size of their proposed update: [1](#0-0) 

The deposit is accepted and kept by the contract (only the excess above `required` is refunded). The `UpdateEntry` written to storage records only the update payload and its byte size — **not** the proposer's account ID or the deposit amount: [2](#0-1) 

When threshold votes are reached and `do_update` is called, it removes the winning entry and then calls `self.entries.clear()` to wipe all remaining proposals: [3](#0-2) 

Because no proposer identity or deposit amount is stored in `UpdateEntry`, there is no mechanism to issue refunds to the proposers of the cleared non-winning entries. Their deposits are permanently locked in the contract.

### Impact Explanation

Every participant whose proposal is cleared by a competing winning update permanently loses their deposit. For a large contract binary (~1.5 MB), `required_deposit` is on the order of tens of NEAR (the test `test_propose_contract_max_size_upload` uses `NearToken::from_near(40)`). With multiple concurrent proposals — a normal governance scenario — the total locked amount can be substantial. There is no admin escape hatch or recovery path once the entries are cleared.

This breaks the production safety/accounting invariant that deposited funds must be returned when the purpose for which they were deposited ceases to exist. Impact: **Medium** — balance/accounting invariant violation causing permanent freezing of participant funds. [4](#0-3) 

### Likelihood Explanation

The scenario is a routine governance operation: multiple participants independently propose different updates (e.g., different binary versions or config changes), then threshold votes converge on one. This requires no adversarial behavior — any honest multi-proposal governance round triggers the loss. Likelihood: **Medium-High**.

### Recommendation

Store the proposer's `AccountId` and the deposit amount inside `UpdateEntry`:

```rust
pub(crate) struct UpdateEntry {
    pub(super) update: Update,
    pub(super) bytes_used: u128,
    pub(super) proposer: AccountId,       // add
    pub(super) deposit: NearToken,        // add
}
```

In `do_update`, before calling `self.entries.clear()`, iterate over the remaining entries and issue a `Promise::new(entry.proposer).transfer(entry.deposit)` for each one. This mirrors the pattern already used in `propose_update` for excess-deposit refunds.

### Proof of Concept

1. Participant A calls `propose_update` with a 1 MB contract binary, attaching ~10 NEAR deposit. `UpdateEntry { update: ..., bytes_used: ... }` is stored — no proposer or deposit recorded.
2. Participant B calls `propose_update` with a different binary, attaching ~10 NEAR deposit.
3. Threshold participants vote for B's proposal via `vote_update`.
4. `do_update` is triggered: it removes B's entry, then calls `self.entries.clear()` — A's entry is silently dropped with no refund.
5. A's ~10 NEAR is permanently locked in the contract. No function exists to recover it. [5](#0-4) [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L1300-1334)
```rust
    pub fn propose_update(
        &mut self,
        #[serializer(borsh)] args: ProposeUpdateArgs,
    ) -> Result<UpdateId, Error> {
        // Only voters can propose updates:
        let proposer = self.voter_or_panic();
        let update: Update = args.try_into()?;

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

        Ok(id)
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
