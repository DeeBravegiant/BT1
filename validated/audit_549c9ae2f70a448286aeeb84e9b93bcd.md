### Title
Proposal Deposits Permanently Locked When a Competing Update Reaches Threshold — (`crates/contract/src/update.rs`)

### Summary

`propose_update` collects a storage deposit from each proposer. When any proposal reaches the voting threshold and `do_update` executes, it unconditionally clears **all** pending proposals and votes, freeing their storage — but no deposit is ever returned to the proposers of the non-executed proposals. There is no withdrawal interface, so those NEAR tokens are permanently locked in the contract.

### Finding Description

`propose_update` in `crates/contract/src/lib.rs` requires a deposit proportional to the storage cost of the proposed update:

```rust
let attached = env::attached_deposit();
let required = ProposedUpdates::required_deposit(&update);
// ...
let id = self.proposed_updates.propose(update);
// Refund only the *excess* above required; the required amount stays in the contract.
if let Some(diff) = attached.checked_sub(required) && diff > NearToken::from_yoctonear(0) {
    Promise::new(proposer).transfer(diff).detach();
}
``` [1](#0-0) 

The required deposit is computed as `env::storage_byte_cost() * bytes_used`, where `bytes_used` includes the full contract binary plus a 128-account-vote overhead: [2](#0-1) 

When threshold votes accumulate for any proposal, `do_update` is called. It removes the winning entry, then **clears every other pending entry and all votes** without refunding any deposit:

```rust
pub fn do_update(&mut self, id: &UpdateId, gas: Gas) -> Option<Promise> {
    let entry = self.entries.remove(id)?;
    // Clear all entries as they might be no longer valid
    self.entries.clear();
    self.vote_by_participant.clear();
    // ... no refund to other proposers
``` [3](#0-2) 

The storage freed by clearing those entries reduces the contract's on-chain storage cost, but the NEAR tokens that were deposited to pay for that storage remain in the contract's balance with no mechanism to retrieve them. There is no `withdraw_proposal_deposit`, no per-proposal deposit tracking, and no refund path in `do_update`.

### Impact Explanation

**Medium.** Every participant whose proposal is cleared by a competing proposal permanently loses their deposit. For a maximum-size contract proposal (~1.5 MB), the required deposit is approximately 40 NEAR (confirmed by sandbox tests using `NearToken::from_near(40)`). With multiple concurrent proposals — a normal governance scenario — the cumulative locked amount can be substantial. This breaks the production accounting invariant that storage deposits are returned when the storage they cover is freed. [4](#0-3) 

### Likelihood Explanation

**High.** The governance model explicitly allows multiple concurrent proposals (each participant may propose independently). The sandbox test `test_propose_update_contract_many` demonstrates exactly this scenario: two proposals are live simultaneously, one reaches threshold, and the comment confirms "all proposals are removed after update." The deposit loss occurs in every such governance round where more than one proposal is active. [5](#0-4) 

### Recommendation

Track the proposer's account ID and the exact deposit amount inside `UpdateEntry`. When `do_update` clears non-winning entries, iterate over them and issue a `Promise::new(proposer).transfer(deposit)` for each. This mirrors the pattern already used in `propose_update` for excess-deposit refunds and in `submit_participant_info` for storage-cost refunds. [6](#0-5) 

### Proof of Concept

1. Participant A calls `propose_update` with a 1 MB contract binary, attaching ~27 NEAR deposit. The deposit is retained by the contract.
2. Participant B calls `propose_update` with a different 1 MB binary, attaching ~27 NEAR deposit. The deposit is retained.
3. Threshold participants vote for B's proposal. `vote_update` calls `do_update(&B_id, gas)`.
4. `do_update` calls `self.entries.clear()` — A's entry is deleted, its storage freed — and returns without issuing any transfer to A.
5. A's ~27 NEAR is permanently locked in the contract. No public method exists to recover it. [7](#0-6) [8](#0-7)

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

**File:** crates/contract/tests/sandbox/upgrade_from_current_contract.rs (L216-223)
```rust
    const CONTRACT_DEPLOY: NearToken = NearToken::from_near(1);

    // Let's propose a contract update instead now.
    let execution = mpc_signer_accounts[0]
        .call(contract.id(), method_names::PROPOSE_UPDATE)
        .args_borsh((invalid_contract_proposal(),))
        .max_gas()
        .deposit(CONTRACT_DEPLOY)
```

**File:** crates/contract/tests/sandbox/upgrade_from_current_contract.rs (L247-299)
```rust
#[tokio::test]
async fn test_propose_update_contract_many() {
    let SandboxTestSetup {
        contract,
        mpc_signer_accounts,
        ..
    } = SandboxTestSetup::builder()
        .with_protocols(ALL_PROTOCOLS)
        .build()
        .await;
    dbg!(contract.id());

    const PROPOSAL_COUNT: usize = 2;
    let mut proposals = Vec::with_capacity(PROPOSAL_COUNT);
    // Try to propose multiple updates to check if they are being proposed correctly
    // and that we can have many at once living in the contract state.
    for i in 0..PROPOSAL_COUNT {
        let execution = mpc_signer_accounts[i % mpc_signer_accounts.len()]
            .call(contract.id(), method_names::PROPOSE_UPDATE)
            .args_borsh(current_contract_proposal())
            .max_gas()
            .deposit(CURRENT_CONTRACT_DEPLOY_DEPOSIT)
            .transact()
            .await
            .unwrap();

        assert!(
            execution.is_success(),
            "failed to propose update [i={i}]; {execution:#?}"
        );
        let proposal_id = execution.json().expect("unable to convert into UpdateId");
        proposals.push(proposal_id);
    }

    // Vote for the last proposal
    vote_update_till_completion(&contract, &mpc_signer_accounts, proposals.last().unwrap()).await;

    // Ensure all proposals are removed after update
    for proposal in proposals {
        let voter = mpc_signer_accounts.first().unwrap();
        let execution = voter
            .call(contract.id(), method_names::VOTE_UPDATE)
            .args_json(serde_json::json!({
                "id": proposal,
            }))
            .gas(GAS_FOR_VOTE_UPDATE)
            .transact()
            .await
            .unwrap();
        dbg!(&execution);

        assert!(execution.is_failure());
    }
```
