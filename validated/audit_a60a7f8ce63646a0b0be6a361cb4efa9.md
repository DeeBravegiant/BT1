### Title
Proposal Deposits Permanently Locked When `do_update` Clears All Competing Entries - (`File: crates/contract/src/update.rs`)

---

### Summary

When `do_update` executes a winning governance proposal, it unconditionally clears **all** pending update entries and votes. Because `UpdateEntry` never records the proposer's account ID or the deposit amount, the NEAR deposits paid by every non-winning proposer are permanently locked in the contract with no refund path and no withdrawal mechanism.

---

### Finding Description

`propose_update` in `crates/contract/src/lib.rs` requires each proposer to attach a deposit proportional to the size of their proposed update (contract binary or config blob). Only the exact excess above `required` is refunded at proposal time; the `required` portion is retained by the contract to cover storage. [1](#0-0) 

The deposit amount and the proposer's identity are **never stored** inside `UpdateEntry`: [2](#0-1) 

When the threshold of votes is reached, `do_update` is called. It removes the winning entry, then calls `self.entries.clear()` and `self.vote_by_participant.clear()`, silently discarding every other pending proposal: [3](#0-2) 

Because neither the proposer's account ID nor the deposit amount is stored in `UpdateEntry`, there is no information available to issue refunds to the displaced proposers. There is no `cancel_proposal`, `withdraw_proposal`, or any other deposit-recovery function in the contract.

---

### Impact Explanation

Every participant who proposed a competing update loses their full deposit permanently. For a maximum-size contract binary (~1.5 MB), `bytes_used` is approximately 1.5 MB + 128 × `size_of::<AccountId>()` overhead, and `required_deposit` converts that to yoctoNEAR at `env::storage_byte_cost()` — roughly 15–40 NEAR per proposal (the sandbox test uses `NearToken::from_near(40)` as a safe upper bound). [4](#0-3) 

If N participants each propose a different contract update and one reaches threshold, the remaining N−1 deposits are locked forever. The contract has no ETH/NEAR withdrawal function, and the storage freed by `entries.clear()` accrues to the contract rather than being returned to proposers. This directly breaks the production accounting invariant that deposited funds must either be used for their stated purpose or refunded.

---

### Likelihood Explanation

The scenario is reachable in normal governance operation. Participants routinely propose competing updates (the test `test_propose_update_contract_many` explicitly exercises multiple simultaneous proposals). A Byzantine participant below the signing threshold can deliberately propose a large contract binary to force other participants to also propose updates, then coordinate the threshold vote to clear all competing proposals and lock their deposits. Even without adversarial intent, any governance round with more than one active proposal triggers the loss.

---

### Recommendation

1. Extend `UpdateEntry` to record the proposer's `AccountId` and the `deposit` amount paid.
2. In `do_update`, before calling `self.entries.clear()`, iterate over all remaining entries and issue a `Promise::new(entry.proposer).transfer(entry.deposit)` for each displaced proposal.
3. Alternatively, store a separate `BTreeMap<UpdateId, (AccountId, NearToken)>` for deposit bookkeeping and drain it on `do_update`.

---

### Proof of Concept

1. Alice (participant) calls `propose_update` with a 1 MB contract binary, attaching 20 NEAR. The contract retains 20 NEAR; `UpdateEntry` stores only `{ update: Contract([...]), bytes_used: ~1_000_000 }` — Alice's identity is gone.
2. Bob (participant) calls `propose_update` with a different 1 MB binary, attaching 20 NEAR. Same result.
3. Threshold participants vote for Bob's proposal. `vote_update` calls `do_update(&bob_id, gas)`.
4. Inside `do_update`: `self.entries.remove(&bob_id)` extracts Bob's entry; `self.entries.clear()` silently drops Alice's entry; `self.vote_by_participant.clear()` drops all votes.
5. Alice's 20 NEAR is now permanently locked in the contract. There is no function to recover it. [5](#0-4) [6](#0-5)

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
