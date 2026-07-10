### Title
Propose-Update Storage Deposits Permanently Stuck When `do_update` Clears All Non-Winning Proposals - (File: `crates/contract/src/update.rs`)

---

### Summary

When `vote_update` reaches threshold and calls `do_update`, the function unconditionally clears **all** pending update proposals and votes via `self.entries.clear()`. Any participant who paid a storage deposit for a non-winning proposal never receives a refund. The freed storage staking flows back into the contract's own account balance, permanently trapping the depositors' NEAR.

---

### Finding Description

`propose_update` in `crates/contract/src/lib.rs` is a `#[payable]` method that requires each proposer to attach a deposit covering the storage cost of their update entry: [1](#0-0) 

The required deposit is computed by `ProposedUpdates::required_deposit`, which for a contract-code update includes the full binary size plus an overhead for 128 participant-vote slots: [2](#0-1) 

At ~17 NEAR per contract-code proposal (confirmed by the test constant `CURRENT_CONTRACT_DEPLOY_DEPOSIT = NearToken::from_millinear(17000)`), these are non-trivial amounts. [3](#0-2) 

When `vote_update` reaches threshold it calls `do_update`, which removes the winning entry and then **bulk-clears every remaining entry and every vote**: [4](#0-3) 

Specifically, lines 199–200:

```rust
self.entries.clear();
self.vote_by_participant.clear();
```

Clearing `entries` frees the on-chain storage that was staked by the non-winning proposers. In NEAR's storage-staking model, freed storage increases the contract's own spendable balance — it does **not** automatically return to the original depositors. There is no refund path, no `cancel_update` method, and no mechanism to recover these deposits after `do_update` runs.

---

### Impact Explanation

Every participant who called `propose_update` for a proposal that was not selected permanently loses their storage deposit. For a contract-code update this is approximately 17 NEAR per non-winning proposal. The NEAR is not destroyed; it silently accretes to the contract's balance, making it unattributable and unrecoverable by the original depositors. This breaks the production accounting invariant that a participant's deposit is returned when their proposal is superseded or discarded — matching the **Medium** allowed impact: *"Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants."*

---

### Likelihood Explanation

The scenario is realistic in any governance round where two or more participants independently propose different contract upgrades (e.g., one proposes a code update, another proposes a config update). Once any single proposal reaches threshold, all competing proposals are silently cleared. No adversarial coordination is required; the loss occurs as a normal side-effect of the governance flow. The only precondition is that more than one participant calls `propose_update` before a threshold vote completes — a routine occurrence in a multi-participant MPC network.

---

### Recommendation

Before calling `self.entries.clear()` in `do_update`, iterate over all remaining entries and schedule a `Promise::new(proposer_account).transfer(deposit)` refund for each non-winning proposer. The proposer's `AccountId` and the deposit amount must be stored in `UpdateEntry` at proposal time (analogous to how `PendingAttestation` stores `attached_deposit`). Alternatively, expose a `cancel_update_proposal(id)` method that lets a proposer withdraw their own proposal and reclaim their deposit before a threshold vote completes.

---

### Proof of Concept

1. Participant A calls `propose_update` with a 2 MB contract binary, attaching ~17 NEAR.
2. Participant B calls `propose_update` with a different 2 MB contract binary, attaching ~17 NEAR.
3. Threshold participants call `vote_update(id_A)`.
4. `vote_update` calls `self.proposed_updates.do_update(&id_A, gas)`.
5. Inside `do_update` (update.rs line 199): `self.entries.clear()` removes Participant B's entry from storage.
6. NEAR runtime releases the storage staking for B's entry back to the contract's balance.
7. Participant B has no method to reclaim their ~17 NEAR. The contract's balance is permanently inflated by that amount. [4](#0-3) [1](#0-0) [5](#0-4)

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

**File:** crates/contract/src/lib.rs (L1343-1388)
```rust
    pub fn vote_update(&mut self, id: UpdateId) -> Result<bool, Error> {
        log!(
            "vote_update: signer={}, id={:?}",
            env::signer_account_id(),
            id,
        );

        let ProtocolContractState::Running(running_state) = &self.protocol_state else {
            env::panic_str("protocol must be in running state");
        };

        let threshold = self.threshold()?;

        let voter = self.voter_or_panic();
        if self.proposed_updates.vote(&id, voter).is_none() {
            return Err(InvalidParameters::UpdateNotFound.into());
        }

        // Filter votes to only count current participants voting for this specific update.
        // This ensures correctness even if the cleanup promise in MpcContract::vote_reshared() fails.
        let valid_votes_count = running_state
            .parameters
            .participants()
            .participants()
            .iter()
            .filter(|(account_id, _, _)| {
                self.proposed_updates
                    .vote_by_participant
                    .get(account_id)
                    .is_some_and(|voted_id| *voted_id == id)
            })
            .count();

        // Not enough votes from current participants, wait for more.
        if (valid_votes_count as u64) < threshold.value() {
            return Ok(false);
        }

        let update_gas_deposit = Gas::from_tgas(self.config.contract_upgrade_deposit_tera_gas);

        let Some(_promise) = self.proposed_updates.do_update(&id, update_gas_deposit) else {
            return Err(InvalidParameters::UpdateNotFound.into());
        };

        Ok(true)
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

**File:** crates/contract/tests/sandbox/utils/consts.rs (L46-46)
```rust
pub const CURRENT_CONTRACT_DEPLOY_DEPOSIT: NearToken = NearToken::from_millinear(17000);
```
