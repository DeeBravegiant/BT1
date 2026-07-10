### Title
Proposal Storage Deposits Permanently Locked When `do_update` Clears Non-Executed Proposals - (File: `crates/contract/src/update.rs`)

### Summary

When `do_update` executes a winning update proposal, it unconditionally clears **all** pending proposals and votes. However, the NEAR storage deposits paid by proposers of the non-executed proposals are never tracked, stored, or refunded. Those deposits are permanently locked in the contract with no recovery path.

### Finding Description

`propose_update` in `crates/contract/src/lib.rs` collects a storage deposit from the proposer, sized proportionally to the update payload: [1](#0-0) 

The deposit is computed by `required_deposit` / `bytes_used` in `crates/contract/src/update.rs`, which for a contract binary includes the full code length: [2](#0-1) 

The `UpdateEntry` struct that is stored records `bytes_used` but **not** the depositor's account ID or the deposit amount: [3](#0-2) 

When threshold votes are reached, `do_update` is called. It removes the winning entry, then calls `self.entries.clear()` and `self.vote_by_participant.clear()` — wiping every other pending proposal — without issuing any refund: [4](#0-3) 

Because neither the depositor's account nor the deposit amount is stored in `UpdateEntry`, there is no information available to issue refunds even if a sweep function were added later. There is no `cancel_proposal`, `withdraw_deposit`, or sweep function anywhere in the contract.

### Impact Explanation

Every participant who proposed a non-winning update loses their storage deposit permanently. For a contract binary update (which can be hundreds of kilobytes), the required deposit is on the order of several NEAR tokens per proposal (NEAR charges ~1 NEAR per 100 KB of storage). With multiple concurrent proposals — a realistic scenario during governance disputes or upgrade races — the total locked amount grows linearly with the number of non-winning proposals. The funds are irrecoverable: they sit in the contract's balance with no accounting entry and no withdrawal path.

This matches the **Medium** allowed impact: *"Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration."*

### Likelihood Explanation

This triggers under entirely normal protocol operation. The test `test_propose_update_contract_many` already demonstrates multiple concurrent proposals being submitted, with one executed and the rest silently discarded. Any governance period where participants disagree on which update to apply — a common real-world scenario — will produce multiple proposals and thus multiple locked deposits. No adversarial action is required; honest participants lose funds as a side-effect of the normal voting flow.

### Recommendation

1. Extend `UpdateEntry` to record the proposer's `AccountId` and the exact deposit amount paid.
2. In `do_update`, before calling `self.entries.clear()`, iterate over all remaining entries and issue `Promise::new(entry.proposer).transfer(entry.deposit)` for each.
3. Alternatively, store a separate `LookupMap<UpdateId, (AccountId, NearToken)>` for deposit accounting and drain it in `do_update`.

### Proof of Concept

**Step 1 – Participant A proposes a large contract update, paying ~5 NEAR deposit:**
```
propose_update(code = <500KB binary>)  // deposit = 5 NEAR, stored in contract
```

**Step 2 – Participant B proposes a different update, also paying ~5 NEAR:**
```
propose_update(code = <different 500KB binary>)  // deposit = 5 NEAR, stored in contract
```

**Step 3 – Threshold participants vote for B's proposal; `vote_update` calls `do_update`:** [5](#0-4) 

`self.entries.clear()` removes A's entry. A's 5 NEAR deposit is never returned. The `UpdateEntry` for A contained no depositor field, so the information needed to refund is gone.

**Result:** Participant A's ~5 NEAR is permanently locked in the MPC contract with no recovery path, directly analogous to the Ammplify JIT penalty lockup where reduced amounts were sent to users but the penalty remainder was left as idle balance with no sweep function.

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
