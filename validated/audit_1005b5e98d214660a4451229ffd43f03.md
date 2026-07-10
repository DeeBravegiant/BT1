### Title
Propose-Update Storage Deposits Are Permanently Lost When a Competing Proposal Wins — (`File: crates/contract/src/update.rs`)

### Summary

When `propose_update` is called, the proposer must attach a storage-staking deposit proportional to the size of the contract binary or config being stored. When any proposal reaches the voting threshold and `do_update` executes, it clears **all** pending proposals and votes without refunding the deposits of the non-winning proposers. Because `UpdateEntry` stores no proposer account ID, there is no mechanism to return those funds. Every participant who proposed a competing update permanently loses their deposit.

### Finding Description

`propose_update` in `crates/contract/src/lib.rs` requires a deposit calculated as `env::storage_byte_cost() × bytes_used(update)`. For a full contract binary (~1.5 MiB), this is on the order of 8–40 NEAR. The exact amount is refunded only if the caller over-attached; the required portion is kept by the contract to cover storage. [1](#0-0) 

The deposit is accepted and the update is stored via `ProposedUpdates::propose`, which inserts an `UpdateEntry` containing only the update payload and `bytes_used` — **no proposer account ID is recorded**. [2](#0-1) [3](#0-2) 

When `vote_update` reaches the threshold, it calls `do_update`, which removes the winning entry and then calls `self.entries.clear()` and `self.vote_by_participant.clear()` — silently discarding every other pending proposal and its associated storage deposit with no refund path. [4](#0-3) 

The unit test `test_proposed_updates_do_update_clears_all_state` explicitly confirms this behavior: all entries and votes are gone after `do_update`, with no deposit accounting. [5](#0-4) 

There is no `withdraw_proposal` function, no refund hook in `do_update`, and no other code path that returns a proposer's deposit. The freed storage accrues to the contract's balance, not to the proposers.

### Impact Explanation

Every participant who submitted a competing `propose_update` call loses their storage deposit permanently when any other proposal wins. For a full contract binary, this is 8–40 NEAR per proposer. The contract's balance grows by the sum of all non-winning deposits, which are irrecoverable. This breaks the production accounting invariant that storage deposits are returned when the storage they cover is freed.

This maps to the allowed Medium impact: *"Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants."*

### Likelihood Explanation

This is not a theoretical edge case. The README explicitly states that multiple proposals can coexist simultaneously, and the sandbox test `test_propose_update_contract_many` demonstrates exactly this scenario. In any governance round where two or more participants independently propose different updates (a normal operational pattern), all but one proposer lose their deposits. No adversarial coordination is required — normal honest operation triggers the loss. [6](#0-5) 

### Recommendation

Store the proposer's `AccountId` inside `UpdateEntry` alongside `bytes_used`. In `do_update`, before calling `self.entries.clear()`, iterate over all remaining entries and schedule a `Promise::new(entry.proposer).transfer(required_deposit(entry.bytes_used))` for each one. This mirrors the refund pattern already used elsewhere in the contract (e.g., `require_deposit` excess refunds and the `on_attestation_verified` timeout-branch refund).

### Proof of Concept

1. Participant A calls `propose_update` with a 1 MiB contract binary, attaching ~10 NEAR deposit. `UpdateEntry { update: A_code, bytes_used: 1_048_576 }` is stored; no proposer field is recorded.
2. Participant B calls `propose_update` with a different 1 MiB binary, attaching ~10 NEAR deposit. A second `UpdateEntry` is stored.
3. Threshold participants vote for B's proposal via `vote_update`.
4. `do_update` is triggered: it removes B's entry, then calls `self.entries.clear()` (removing A's entry) and `self.vote_by_participant.clear()`.
5. A's 10 NEAR deposit is now permanently held by the contract. A has no recourse — no withdrawal function exists, and the freed storage bytes are not credited back to A. [7](#0-6) [8](#0-7)

### Citations

**File:** crates/contract/src/lib.rs (L1297-1334)
```rust
    /// Propose update to either code or config, but not both of them at the same time.
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

**File:** crates/contract/src/update.rs (L167-174)
```rust
    pub fn propose(&mut self, update: Update) -> UpdateId {
        let bytes_used = bytes_used(&update);

        let id = self.id.generate();
        self.entries.insert(id, UpdateEntry { update, bytes_used });

        id
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

**File:** crates/contract/src/update.rs (L547-615)
```rust
    /// Asserts that [`ProposedUpdates::do_update`] clears all entries and votes.
    #[test]
    fn test_proposed_updates_do_update_clears_all_state() {
        // Given: multiple update proposals with votes from different accounts
        let mut proposed_updates = ProposedUpdates::default();

        let update_0 = Update::Contract([0; 1000].into());
        let update_id_0 = proposed_updates.propose(update_0.clone());

        let update_1 = Update::Contract([1; 1000].into());
        let update_id_1 = proposed_updates.propose(update_1.clone());

        let update_2 = Update::Config(dummy_config(1));
        let update_id_2 = proposed_updates.propose(update_2.clone());

        let account_0 = gen_account_id();
        let account_1 = gen_account_id();
        let account_2 = gen_account_id();

        proposed_updates.vote(&update_id_0, account_0.clone());
        proposed_updates.vote(&update_id_1, account_1.clone());
        proposed_updates.vote(&update_id_2, account_2.clone());

        let before: TestUpdateVotes = (&proposed_updates).try_into().unwrap();
        let expected_before = TestUpdateVotes {
            id: 3,
            votes: BTreeMap::from([
                (account_0.clone(), 0),
                (account_1.clone(), 1),
                (account_2.clone(), 2),
            ]),
            entries: BTreeMap::from([
                (
                    0,
                    UpdateEntry {
                        update: update_0.clone(),
                        bytes_used: bytes_used(&update_0),
                    },
                ),
                (
                    1,
                    UpdateEntry {
                        update: update_1.clone(),
                        bytes_used: bytes_used(&update_1),
                    },
                ),
                (
                    2,
                    UpdateEntry {
                        update: update_2.clone(),
                        bytes_used: bytes_used(&update_2),
                    },
                ),
            ]),
        };
        assert_eq!(before, expected_before);

        // When: executing an update
        proposed_updates.do_update(&update_id_1, Gas::from_tgas(100));

        // Then: all state is cleared (entries and votes)
        let after: TestUpdateVotes = (&proposed_updates).try_into().unwrap();
        let expected_after = TestUpdateVotes {
            id: 3,
            votes: BTreeMap::new(),
            entries: BTreeMap::new(),
        };
        assert_eq!(after, expected_after);
    }
```

**File:** crates/contract/tests/sandbox/upgrade_from_current_contract.rs (L248-304)
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

    // Let's check that we can call into the state and see all the proposals.
    let state: ProtocolContractState = get_state(&contract).await;
    dbg!(state);
}
```
