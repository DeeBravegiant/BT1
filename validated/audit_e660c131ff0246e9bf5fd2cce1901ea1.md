### Title
Unrecoverable Storage Deposits for Cleared Update Proposals — (`File: crates/contract/src/update.rs`)

### Summary

When `do_update` executes a winning governance proposal, it unconditionally clears **all** pending proposals and their associated storage. The NEAR tokens paid as storage deposits by the proposers of the cleared (non-winning) proposals are absorbed into the contract's general balance with no refund path and no withdrawal mechanism, permanently freezing those funds.

### Finding Description

`propose_update` requires a deposit proportional to the serialized size of the proposed update: [1](#0-0) [2](#0-1) 

For a full contract binary (~1.5 MB), `required_deposit` can reach approximately 40 NEAR. The deposit is retained by the contract to cover on-chain storage.

When any update reaches the voting threshold, `do_update` is called: [3](#0-2) 

`self.entries.clear()` frees the storage occupied by every pending proposal. On NEAR, freed storage returns its staked tokens to the **contract's own balance**, not to the original depositors. The proposers of the cleared entries have no mechanism to reclaim their deposits: there is no refund call inside `do_update`, no withdrawal endpoint in the contract's public API, and no record of which account paid for which entry.

The `propose_update` call site refunds only the *excess* above the required deposit at submission time: [4](#0-3) 

There is no corresponding refund when the entry is later cleared.

The test `test_propose_update_contract_many` explicitly demonstrates multiple concurrent proposals: [5](#0-4) 

When the last proposal wins, all others are cleared and their deposits are silently absorbed.

### Impact Explanation

Every participant whose proposal is cleared by a competing update permanently loses their storage deposit. For a full contract binary, this is ~40 NEAR per proposal. The tokens are not stolen by an attacker but are irrecoverably locked inside the contract with no withdrawal path. This breaks the accounting invariant that a proposer's deposit is recoverable if their proposal is not executed. The impact matches: **Medium — balance/accounting invariant broken without requiring network-level DoS or operator misconfiguration.**

### Likelihood Explanation

Multiple concurrent proposals are a realistic governance scenario: participants may disagree on which upgrade to apply, or a participant may propose a config change while another proposes a code upgrade. The clearing is automatic and unconditional on every successful `vote_update`. Any participant (voter) can trigger the loss by casting the threshold vote for a competing proposal, with no special privilege required beyond being an active participant.

### Recommendation

In `do_update`, before calling `self.entries.clear()`, iterate over all remaining entries and issue a `Promise::new(proposer).transfer(deposit)` refund for each. Because the contract does not currently store the proposer's `AccountId` alongside the entry, `UpdateEntry` should be extended to record it at proposal time. Alternatively, track a `proposer → deposit` map in `ProposedUpdates` and drain it during cleanup.

### Proof of Concept

1. Participant A calls `propose_update` with a 1.5 MB contract binary, attaching ~40 NEAR.
2. Participant B calls `propose_update` with a different binary, attaching ~40 NEAR.
3. Threshold participants vote for B's proposal; `vote_update` calls `do_update(&B_id, gas)`.
4. Inside `do_update`: `self.entries.remove(&B_id)` removes B's entry; `self.entries.clear()` removes A's entry. [6](#0-5) 
5. A's ~40 NEAR is now part of the contract's balance. No refund is issued. A has no endpoint to call to recover the funds.

### Citations

**File:** crates/contract/src/update.rs (L162-164)
```rust
    pub fn required_deposit(update: &Update) -> NearToken {
        required_deposit(bytes_used(update))
    }
```

**File:** crates/contract/src/update.rs (L195-226)
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
```

**File:** crates/contract/src/update.rs (L297-299)
```rust
fn required_deposit(bytes_used: u128) -> NearToken {
    env::storage_byte_cost().saturating_mul(bytes_used)
}
```

**File:** crates/contract/src/lib.rs (L1326-1331)
```rust
        // Refund the difference if the proposer attached more than required.
        if let Some(diff) = attached.checked_sub(required)
            && diff > NearToken::from_yoctonear(0)
        {
            Promise::new(proposer).transfer(diff).detach();
        }
```

**File:** crates/contract/tests/sandbox/upgrade_from_current_contract.rs (L247-283)
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

```
