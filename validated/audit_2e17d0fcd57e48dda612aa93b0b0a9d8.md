### Title
Permanent Loss of Proposer Deposit in `propose_update()` — No Withdrawal or Refund Path When Proposals Are Cleared - (File: `crates/contract/src/update.rs`, `crates/contract/src/lib.rs`)

### Summary

`propose_update()` requires a storage-staking deposit proportional to the size of the proposed contract binary or config. This deposit is never returned to the proposer under any outcome: neither when the proposal is executed, nor when it is superseded or cleared by a competing update. There is no `cancel_update()`, no deposit-refund path, and no mechanism for the proposer to recover their locked NEAR. This is a direct analog to the `depositUnderlying()` / missing `withdrawUnderlying()` pattern in the reference report.

---

### Finding Description

`propose_update()` in `crates/contract/src/lib.rs` computes a required deposit via `ProposedUpdates::required_deposit()`, which calls `bytes_used()`: [1](#0-0) 

The `bytes_used()` function in `crates/contract/src/update.rs` sizes the deposit as the serialized update payload plus a fixed overhead for 128 participant-vote slots: [2](#0-1) 

For a typical contract WASM binary (hundreds of KB), this deposit is several NEAR tokens. The contract only refunds the **excess** above the required amount; the required portion is permanently retained.

When `do_update()` is triggered by a threshold of votes, it calls `entries.clear()` and `vote_by_participant.clear()`, freeing all stored proposals — including those from proposers who never had their update executed: [3](#0-2) 

Freeing storage returns the storage-staking NEAR to the **contract's** balance, not to the original proposers. No refund transfer is issued to any proposer at any point in `do_update()`. The contract API exposes no `cancel_update()`, no `withdraw_update_deposit()`, and no deposit-recovery path. The only related cleanup functions — `remove_update_vote()` and `remove_non_participant_update_votes()` — remove votes only, not proposal entries or deposits: [4](#0-3) [5](#0-4) 

The method name registry confirms no cancel or deposit-withdrawal endpoint exists: [6](#0-5) 

---

### Impact Explanation

Every participant who calls `propose_update()` permanently loses their deposit. In a typical upgrade cycle, multiple participants may propose competing updates (the test `test_propose_update_contract_many` and `only_one_vote_from_participant` demonstrate this pattern). When any one update reaches threshold and `do_update()` fires, all other proposals are cleared via `entries.clear()` with no refund to their proposers. The freed storage staking accrues to the contract's balance. This breaks the production accounting invariant that storage-staking deposits are recoverable when the storage is freed.

**Impact class:** Medium — balance/accounting invariant break. Participant funds controlled by the MPC chain-signature contract are permanently absorbed with no recovery path.

---

### Likelihood Explanation

Every contract upgrade cycle involves at least one `propose_update()` call. In practice, multiple participants propose competing updates (as shown in sandbox tests). The deposit for a contract binary update is several NEAR tokens. This loss occurs deterministically on every upgrade, not as a rare edge case.

---

### Recommendation

1. **Track the proposer's account** in `UpdateEntry` alongside `bytes_used`.
2. In `do_update()`, before calling `entries.clear()`, iterate over all non-executed entries and issue a `Promise::new(entry.proposer).transfer(refund_amount)` for each, where `refund_amount = storage_byte_cost * entry.bytes_used`.
3. Alternatively, add a `cancel_update(id: UpdateId)` endpoint (restricted to the original proposer) that removes the entry and refunds the deposit, analogous to the recommendation in the reference report to add `withdrawUnderlying()`.

---

### Proof of Concept

1. Participant A calls `propose_update()` with a 500 KB WASM binary, attaching ~5 NEAR deposit.
2. Participant B calls `propose_update()` with a different WASM binary, attaching ~5 NEAR deposit.
3. Threshold participants vote for B's proposal; `vote_update()` calls `do_update()`.
4. `do_update()` calls `entries.clear()` — A's entry is removed, storage freed, freed staking goes to contract balance.
5. A's 5 NEAR deposit is permanently absorbed by the contract. A has no function to call to recover it. [7](#0-6) [8](#0-7)

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

**File:** crates/contract/src/lib.rs (L1395-1404)
```rust
    /// Removes an update vote by the caller
    /// panics if the contract is not in a running state or if the caller is not a participant
    pub fn remove_update_vote(&mut self) {
        log!("remove_update_vote: signer={}", env::signer_account_id(),);
        let ProtocolContractState::Running(_running_state) = &self.protocol_state else {
            env::panic_str("protocol must be in running state");
        };
        let voter = self.voter_or_panic();
        self.proposed_updates.remove_vote(&voter);
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

**File:** crates/contract/src/update.rs (L229-253)
```rust
    /// Removes the vote for [`AccountId`].
    pub fn remove_vote(&mut self, voter: &AccountId) {
        self.vote_by_participant.remove(voter);
    }

    /// Removes votes from the specified accounts.
    pub fn remove_votes(&mut self, accounts_to_remove: &[AccountId]) {
        accounts_to_remove
            .iter()
            .for_each(|account| self.remove_vote(account));
    }

    /// Removes votes from accounts that are not participants.
    pub fn remove_non_participant_votes(&mut self, participants: &Participants) {
        // Note: This operation has quadratic time complexity.
        // TODO(#1572): optimize quadratic time complexity
        let non_participants: Vec<AccountId> = self
            .vote_by_participant
            .keys()
            .filter(|voter| !participants.is_participant_given_account_id(voter))
            .cloned()
            .collect();

        self.remove_votes(&non_participants);
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

**File:** crates/near-mpc-contract-interface/src/method_names.rs (L35-36)
```rust
pub const REMOVE_UPDATE_VOTE: &str = "remove_update_vote";
pub const REMOVE_NON_PARTICIPANT_UPDATE_VOTES: &str = "remove_non_participant_update_votes";
```
