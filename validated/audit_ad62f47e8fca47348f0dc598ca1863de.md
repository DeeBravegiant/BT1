Looking at the `propose_update` / `do_update` flow in `crates/contract/src/update.rs` and `crates/contract/src/lib.rs`, I can identify a direct structural analog to the staking-rewards zero-supply loss.

---

### Title
Proposal Deposits Permanently Trapped in Contract When `do_update` Clears All Competing Entries — (`File: crates/contract/src/update.rs`)

### Summary

When any contract update reaches threshold and `do_update` is executed, it unconditionally clears **all** pending proposals and votes. The NEAR deposits paid by proposers of non-winning proposals to cover storage costs are never refunded. Because no withdrawal or cancellation path exists, those tokens are permanently absorbed into the contract's balance with no recovery mechanism — a direct analog to the staking-rewards zero-supply loss where value accumulates in a state that makes it permanently inaccessible.

### Finding Description

`propose_update` requires a deposit proportional to the byte-size of the proposed update to cover on-chain storage: [1](#0-0) 

The deposit is computed by `ProposedUpdates::required_deposit`, which scales with `bytes_used(update)`. For a full WASM binary (~1.5 MB), this is on the order of tens of NEAR tokens: [2](#0-1) 

When any update reaches threshold, `do_update` is called. It removes the winning entry, then **unconditionally clears every other entry and every vote**: [3](#0-2) 

The cleared entries' storage is freed (reducing the contract's storage usage), but the NEAR tokens deposited by the non-winning proposers are **never transferred back** to them. There is no `cancel_proposal`, `withdraw_deposit`, or any other reclaim path in the contract. The tokens remain in the contract's balance indefinitely.

The same loss occurs via `remove_non_participant_votes` after resharing: it removes votes but leaves proposals (and their locked deposits) in place, and there is still no refund path: [4](#0-3) 

### Impact Explanation

Every time a contract upgrade is executed in a multi-proposal scenario, all non-winning proposers permanently lose their storage deposits. For a full WASM binary proposal, this is tens of NEAR per proposer. The funds are absorbed into the contract's balance and are unrecoverable by any on-chain path. This breaks the accounting invariant that storage deposits should be returned when the storage they paid for is freed.

This maps to **Medium** under the allowed scope: *"Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants."*

### Likelihood Explanation

In any production upgrade cycle where multiple participants independently propose competing updates (a normal governance pattern), the losing proposers lose their deposits. The `test_propose_update_contract_many` sandbox test explicitly exercises multiple concurrent proposals and confirms all are cleared after one wins — but never checks that deposits are refunded. The condition is reachable by any participant without threshold collusion. [5](#0-4) 

### Recommendation

In `do_update`, before calling `self.entries.clear()`, iterate over all remaining entries and issue a `Promise::new(original_proposer).transfer(deposit)` for each one. The proposer's account ID must be stored in `UpdateEntry` at proposal time. Alternatively, implement a `cancel_proposal` method that allows a proposer to withdraw their deposit and remove their entry at any time before execution.

### Proof of Concept

1. Participant A calls `propose_update` with a 1.5 MB WASM binary, attaching ~15 NEAR as the required deposit. Entry `id=0` is stored.
2. Participant B calls `propose_update` with a different WASM binary, attaching ~15 NEAR. Entry `id=1` is stored.
3. Enough participants vote for `id=1` to reach threshold. `vote_update` calls `do_update(&id=1, gas)`.
4. Inside `do_update`: entry `id=1` is removed and executed; `self.entries.clear()` removes entry `id=0`; `self.vote_by_participant.clear()` removes all votes.
5. Participant A's 15 NEAR deposit is now in the contract's balance. No function exists to recover it. The loss is permanent. [6](#0-5)

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

**File:** crates/contract/src/lib.rs (L1798-1800)
```rust
        self.proposed_updates
            .remove_non_participant_votes(participants);
        Ok(())
```

**File:** crates/contract/src/update.rs (L161-174)
```rust
impl ProposedUpdates {
    pub fn required_deposit(update: &Update) -> NearToken {
        required_deposit(bytes_used(update))
    }

    /// Propose an update given the new contract code and/or config.
    pub fn propose(&mut self, update: Update) -> UpdateId {
        let bytes_used = bytes_used(&update);

        let id = self.id.generate();
        self.entries.insert(id, UpdateEntry { update, bytes_used });

        id
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

**File:** crates/contract/tests/sandbox/upgrade_from_current_contract.rs (L248-299)
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
