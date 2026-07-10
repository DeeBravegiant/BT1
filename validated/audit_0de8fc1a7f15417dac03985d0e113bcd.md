### Title
Deposits from Non-Executed Update Proposals Are Permanently Lost When Any Update Is Executed - (File: `crates/contract/src/update.rs`)

### Summary
When `do_update` executes a contract upgrade, it unconditionally clears **all** pending proposals and votes but never refunds the NEAR deposits paid by proposers of the non-executed updates. Because `UpdateEntry` does not record the proposer's account ID or deposit amount, there is no mechanism to issue refunds. Every participant who proposed a competing update loses their deposit permanently.

### Finding Description
`propose_update` requires each proposer to attach a deposit proportional to the storage their proposal occupies:

```rust
// crates/contract/src/lib.rs ~1308-1331
let required = ProposedUpdates::required_deposit(&update);
if attached < required { return Err(...InsufficientDeposit...); }
let id = self.proposed_updates.propose(update);
// Only excess above `required` is refunded; the required portion is kept.
if let Some(diff) = attached.checked_sub(required) && diff > NearToken::from_yoctonear(0) {
    Promise::new(proposer).transfer(diff).detach();
}
```

The deposit is absorbed into the contract's balance. When threshold votes are reached and `do_update` fires:

```rust
// crates/contract/src/update.rs ~195-226
pub fn do_update(&mut self, id: &UpdateId, gas: Gas) -> Option<Promise> {
    let entry = self.entries.remove(id)?;
    // Clear all entries as they might be no longer valid
    self.entries.clear();          // ← all competing proposals wiped
    self.vote_by_participant.clear();
    // No refund loop here
    ...
}
```

`UpdateEntry` stores only the update payload and `bytes_used`; it does not record the proposer's identity or the deposit amount:

```rust
// crates/contract/src/update.rs ~132-135
pub(crate) struct UpdateEntry {
    pub(super) update: Update,
    pub(super) bytes_used: u128,
    // no proposer AccountId, no deposit field
}
```

Because the proposer is not stored, the contract cannot issue refunds even in principle. The NEAR deposited for every cleared proposal is permanently locked in the contract's balance.

### Impact Explanation
This breaks the production accounting invariant that storage deposits must be returned when the storage they cover is freed. For a contract-code update, `bytes_used` includes the full wasm binary (potentially hundreds of kilobytes to megabytes), making the required deposit on the order of several NEAR per proposal. Every participant who proposed a competing update loses that deposit permanently when any other update is executed. This constitutes a direct, permanent loss of funds held by the chain-signature contract, fitting the **Medium** impact category: *balance/accounting invariant broken without relying on network-level DoS or operator misconfiguration*.

### Likelihood Explanation
In any production resharing or upgrade cycle where more than one participant independently proposes an update (a routine governance scenario), the losing proposers automatically forfeit their deposits. No adversarial action is required; the loss is a deterministic consequence of normal protocol operation.

### Recommendation
1. Add `proposer: AccountId` and `deposit: NearToken` fields to `UpdateEntry`.
2. In `do_update`, iterate over all entries being cleared and issue `Promise::new(entry.proposer).transfer(entry.deposit)` for each before calling `self.entries.clear()`.
3. Alternatively, maintain a separate `BTreeMap<UpdateId, (AccountId, NearToken)>` for deposit tracking so that refunds survive the entry-clear path.

### Proof of Concept
1. Participant A calls `propose_update` with a 500 KB contract binary, attaching ~5 NEAR deposit. [1](#0-0) 
2. Participant B calls `propose_update` with a different 500 KB binary, attaching ~5 NEAR deposit.
3. Threshold participants vote for A's update via `vote_update`.
4. `vote_update` calls `self.proposed_updates.do_update(&id, ...)`. [2](#0-1) 
5. `do_update` removes A's entry, then calls `self.entries.clear()` — B's entry is deleted with no refund. [3](#0-2) 
6. B's ~5 NEAR deposit is permanently locked in the contract's balance. `UpdateEntry` contains no proposer field, so no refund path exists. [4](#0-3)

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

**File:** crates/contract/src/lib.rs (L1381-1387)
```rust
        let update_gas_deposit = Gas::from_tgas(self.config.contract_upgrade_deposit_tera_gas);

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
