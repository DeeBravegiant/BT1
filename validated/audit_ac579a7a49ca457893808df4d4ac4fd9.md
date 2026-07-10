### Title
Deposit Funds Permanently Frozen When `do_update` Clears All Pending Proposals Without Refunding Proposers - (File: `crates/contract/src/update.rs`)

---

### Summary

When a contract update reaches threshold votes and `do_update` is executed, it unconditionally clears **all** pending proposals via `self.entries.clear()`. Because `UpdateEntry` does not record the proposer's account ID, the NEAR token deposits paid by proposers of non-winning proposals are permanently frozen in the contract with no refund path.

---

### Finding Description

`propose_update` in `lib.rs` requires each proposer to attach a deposit proportional to the storage cost of their update payload: [1](#0-0) 

The required deposit is retained in the contract (only the excess is refunded): [2](#0-1) 

The `UpdateEntry` struct that is stored for each proposal contains only the update payload and its byte size — **no proposer account ID**: [3](#0-2) 

When `do_update` is triggered by a threshold vote, it removes the winning entry and then calls `self.entries.clear()`, which silently discards every other pending proposal: [4](#0-3) 

Because the proposer's identity was never stored in `UpdateEntry`, there is no mechanism to issue refunds for the cleared proposals. The NEAR tokens that funded those proposals remain in the contract balance with no way to recover them.

---

### Impact Explanation

Every participant who proposed a non-winning update loses their full required deposit permanently. For a large contract binary (e.g., 100 KB–1 MB), the storage-based deposit can reach tens to hundreds of NEAR tokens per proposal. With multiple concurrent proposals — a normal operational scenario — the total frozen amount compounds. The funds are not transferred anywhere; they simply accumulate in the contract balance with no withdrawal path, satisfying the "permanent freezing of funds" criterion.

---

### Likelihood Explanation

The scenario is a routine operational event: participants routinely propose competing contract upgrades. The `do_update` clear-all path is exercised every time any update reaches threshold. No adversarial intent is required — the loss occurs automatically as a side-effect of normal governance. The only precondition is that two or more proposals exist simultaneously when a threshold vote completes, which is explicitly tested and expected behavior. [5](#0-4) 

---

### Recommendation

1. Add a `proposer: AccountId` field to `UpdateEntry` so the refund target is always available.
2. In `do_update`, before calling `self.entries.clear()`, iterate over all remaining entries and issue `Promise::new(entry.proposer).transfer(required_deposit(entry.bytes_used))` for each one.
3. Alternatively, maintain a separate `proposer_by_update_id: IterableMap<UpdateId, AccountId>` map that is drained with refunds before clearing.

---

### Proof of Concept

1. Participant A calls `propose_update` with a 500 KB contract binary. `required_deposit` ≈ 50 NEAR. The contract retains 50 NEAR; only excess is refunded.
2. Participant B calls `propose_update` with a different binary. Same deposit retained.
3. Threshold participants vote for B's update via `vote_update`. The threshold is reached.
4. `do_update(&id_B, gas)` is called:
   - Line 196: `self.entries.remove(&id_B)` — removes B's entry.
   - Line 199: `self.entries.clear()` — silently removes A's entry. No refund issued.
   - Line 200: `self.vote_by_participant.clear()`.
5. A's 50 NEAR deposit is now permanently frozen in the contract. `UpdateEntry` for A contained no `proposer` field, so no refund promise was ever constructed. [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L1308-1316)
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

**File:** crates/contract/tests/sandbox/upgrade_from_current_contract.rs (L248-283)
```rust
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
