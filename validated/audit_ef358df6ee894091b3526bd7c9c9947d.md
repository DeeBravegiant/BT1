### Title
Proposal Deposits in `propose_update` Are Permanently Locked With No Refund or Withdrawal Path — (`File: crates/contract/src/update.rs`, `crates/contract/src/lib.rs`)

### Summary
Every call to `propose_update` requires the proposing participant to attach a NEAR deposit sized to cover estimated storage for the update entry. When `do_update` executes, it clears **all** pending entries and votes — including proposals that were never selected — but never refunds any of the attached deposits. There is no withdrawal, cancel, or refund method anywhere in the contract. The deposited NEAR is permanently locked in the contract balance.

### Finding Description

`propose_update` charges a deposit calculated by `ProposedUpdates::required_deposit`: [1](#0-0) 

`bytes_used` over-estimates storage (it pre-allocates for 128 participant votes plus the full WASM binary), so the deposit for a contract upgrade can be several NEAR: [2](#0-1) 

`propose_update` collects the deposit and refunds only the *excess* above the required amount — the required portion is kept by the contract: [3](#0-2) 

When threshold votes are reached and `vote_update` triggers `do_update`, **all** entries (including non-winning proposals) are cleared without any refund: [4](#0-3) 

The storage is freed (NEAR runtime reclaims the bytes), but the deposited tokens remain in the contract's balance. There is no `cancel_proposal`, `withdraw_deposit`, or owner-withdrawal method anywhere in the contract. [5](#0-4) 

`remove_update_vote` only removes the vote record, not the proposal entry or its deposit.

### Impact Explanation

Every `propose_update` call permanently locks NEAR tokens in the contract. For a contract upgrade with a 1 MB WASM binary, `bytes_used` yields roughly 1 MB + overhead, and at NEAR's storage byte cost (~10 yoctoNEAR/byte) this is on the order of **10 NEAR per proposal**. When multiple competing proposals exist (normal governance operation), all non-winning proposals' deposits are silently cleared and lost. The winning proposal's deposit is also never returned. Over the lifetime of the contract, this accumulates as irrecoverable value loss for the MPC participants and the protocol.

This breaks the production accounting invariant that storage-staking deposits are refundable when the underlying storage is freed.

### Likelihood Explanation

`propose_update` is a routine governance operation. Every contract upgrade or config change requires it. The deposit loss is certain and unconditional — it occurs on every successful call, not just under adversarial conditions.

### Recommendation

1. Track the depositor's `AccountId` and the exact deposit amount inside `UpdateEntry`.
2. When `do_update` clears entries (both the winning and all competing proposals), schedule `Promise::new(depositor).transfer(deposit)` for each cleared entry.
3. Add a `cancel_proposal(id: UpdateId)` method callable by the original proposer that removes the entry and refunds the deposit.

### Proof of Concept

1. Participant A calls `propose_update` with a 500 KB WASM binary, attaching ~5 NEAR deposit.
2. Participant B calls `propose_update` with a different WASM, attaching ~5 NEAR deposit.
3. Threshold participants vote for proposal A; `vote_update` triggers `do_update`.
4. `do_update` calls `self.entries.clear()` — proposal B's entry is removed, its 5 NEAR deposit is gone.
5. Proposal A's entry is also removed (via `self.entries.remove(id)`), its 5 NEAR deposit is also gone.
6. Both participants have permanently lost their deposits. The contract balance increased by ~10 NEAR with no path to recover it. [6](#0-5)

### Citations

**File:** crates/contract/src/update.rs (L161-164)
```rust
impl ProposedUpdates {
    pub fn required_deposit(update: &Update) -> NearToken {
        required_deposit(bytes_used(update))
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
