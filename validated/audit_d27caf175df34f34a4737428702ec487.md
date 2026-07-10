### Title
Proposal Deposits for Non-Executed Updates Are Permanently Locked on `do_update` Sweep - (File: `crates/contract/src/update.rs`)

### Summary

When `vote_update` reaches threshold and triggers `do_update`, the implementation clears **all** pending proposal entries without refunding the storage deposits paid by proposers of the non-executed proposals. Those NEAR tokens are permanently locked in the contract with no recovery path.

### Finding Description

`propose_update` in `crates/contract/src/lib.rs` requires each proposer to attach a deposit proportional to the size of their update payload (contract bytecode or config JSON), calculated as `storage_byte_cost × bytes_used`. For a typical contract binary (~1 MB), this is on the order of 10 NEAR per proposal. [1](#0-0) 

The deposit is retained by the contract to cover on-chain storage for the proposal entry. When any one proposal reaches the voting threshold, `vote_update` calls `do_update`, which removes the winning entry and then unconditionally clears **all remaining entries**: [2](#0-1) 

The `entries.clear()` call drops every competing proposal from storage, but no refund promise is issued for any of them. The deposits those proposers paid are now held by the contract account with no mechanism to retrieve them — there is no `withdraw`, `cancel_proposal`, or `refund_proposal_deposit` endpoint.

The deposit magnitude is set by: [3](#0-2) 

For a 1 MB contract binary, `bytes_used` ≈ 1,048,576 + overhead bytes, yielding a required deposit of roughly 10 NEAR at current NEAR storage pricing. Multiple participants can each propose a different update simultaneously (the governance flow explicitly supports this — each participant can vote for exactly one proposal at a time), so the total locked amount scales with the number of competing proposals.

### Impact Explanation

Every participant whose proposal is swept by `do_update` permanently loses their deposit. The contract has no owner-withdraw, fee-accrual, or refund path for these funds. The accounting invariant — "a proposer's deposit is returned if their proposal is not executed" — is broken. This matches the Medium allowed impact: *balance or contract execution-flow manipulation that breaks production safety/accounting invariants*.

### Likelihood Explanation

The governance flow is designed to allow multiple simultaneous proposals. The README and tests confirm that participants routinely propose competing updates. [4](#0-3) 

Any time two or more proposals coexist and one reaches threshold, the non-winning proposers lose their deposits. This is a normal operational scenario, not an edge case.

### Recommendation

Before calling `entries.clear()` in `do_update`, iterate over the remaining entries and issue a `Promise::new(proposer_account_id).transfer(deposit)` for each one. Because `propose_update` does not currently store the proposer's `AccountId` alongside the entry, the `UpdateEntry` struct must be extended to record the proposer and the exact deposit amount at proposal time, so the refund can be issued correctly during the sweep. [5](#0-4) 

### Proof of Concept

1. Participant A calls `propose_update` with a 1 MB contract binary, attaching ~10 NEAR deposit. Proposal ID = 0 is stored.
2. Participant B calls `propose_update` with a different 1 MB binary, attaching ~10 NEAR deposit. Proposal ID = 1 is stored.
3. A threshold of participants vote for proposal 1 via `vote_update(id=1)`.
4. `do_update` is invoked: it removes entry 1, then calls `self.entries.clear()` — silently dropping entry 0 — and `self.vote_by_participant.clear()`.
5. Participant A's ~10 NEAR deposit is now held by the contract account with no way to recover it. The contract has no withdraw or refund endpoint for proposal deposits. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/contract/src/lib.rs (L1298-1334)
```rust
    #[payable]
    #[handle_result]
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

**File:** crates/contract/tests/sandbox/upgrade_from_current_contract.rs (L479-489)
```rust
    let execution = mpc_signer_accounts[0]
        .call(contract.id(), method_names::PROPOSE_UPDATE)
        .args_borsh(current_contract_proposal())
        .max_gas()
        .deposit(CURRENT_CONTRACT_DEPLOY_DEPOSIT)
        .transact()
        .await
        .unwrap();
    dbg!(&execution);
    assert!(execution.is_success());
    let proposal_b: UpdateId = execution.json().unwrap();
```
