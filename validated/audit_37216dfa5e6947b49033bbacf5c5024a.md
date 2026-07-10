### Title
Deposits for Non-Winning Update Proposals Are Permanently Frozen When `do_update` Clears All Entries — (`File: crates/contract/src/update.rs`)

---

### Summary

When a governance update proposal reaches threshold votes and `do_update` executes, it unconditionally clears **all** pending proposals from `ProposedUpdates::entries`. The deposits paid by proposers of non-winning proposals are never refunded, and because `UpdateEntry` does not record the depositor's account ID, there is no recovery path. The NEAR tokens are permanently frozen inside the contract.

---

### Finding Description

`propose_update` in `crates/contract/src/lib.rs` requires a storage-staking deposit proportional to the size of the proposed update (contract binary or config). The deposit is computed by `ProposedUpdates::required_deposit` and taken from the caller: [1](#0-0) 

The deposit amount is calculated from `bytes_used` and stored in `UpdateEntry`, but **the depositor's account ID is never stored**: [2](#0-1) 

When threshold votes are reached and `do_update` is called, it removes the winning entry and then calls `self.entries.clear()` and `self.vote_by_participant.clear()` — wiping all other pending proposals without issuing any refunds: [3](#0-2) 

The contract README explicitly documents this behavior: "all pending update proposals and votes are cleared as they are no longer valid after the contract migration." [4](#0-3) 

Because `UpdateEntry` stores only `update` and `bytes_used` — not the proposer's `AccountId` — even a future contract upgrade cannot reconstruct who to refund. The frozen NEAR tokens have no recovery path.

---

### Impact Explanation

Every participant who proposed a non-winning update loses their storage deposit permanently. For a large contract binary (e.g., 500 KB), `required_deposit` = `env::storage_byte_cost() × bytes_used` can amount to several NEAR tokens per proposal. With multiple concurrent proposals (a normal operational scenario), the total frozen amount scales linearly. The funds are held in the MPC contract's balance with no accounting record of their origin.

This matches the allowed impact: **permanent freezing of funds controlled by the MPC contract**.

---

### Likelihood Explanation

The scenario requires no malicious actor. It fires in any normal governance round where:
1. Two or more participants each propose a different update (e.g., different contract binaries or configs).
2. One proposal reaches threshold votes.
3. `do_update` clears all entries.

This is a routine operational pattern — participants routinely propose competing updates. The trigger is a standard governance action by honest participants below the signing threshold.

---

### Recommendation

1. **Store the depositor's `AccountId` and deposit amount in `UpdateEntry`**:
   ```rust
   pub(crate) struct UpdateEntry {
       pub(super) update: Update,
       pub(super) bytes_used: u128,
       pub(super) proposer: AccountId,       // add
       pub(super) deposit: NearToken,        // add
   }
   ```

2. **Refund all cleared entries in `do_update`** before calling `self.entries.clear()`:
   ```rust
   pub fn do_update(&mut self, id: &UpdateId, gas: Gas) -> Option<Promise> {
       let entry = self.entries.remove(id)?;
       // Refund all non-winning proposals before clearing
       for (_, e) in self.entries.iter() {
           Promise::new(e.proposer.clone()).transfer(e.deposit).detach();
       }
       self.entries.clear();
       self.vote_by_participant.clear();
       // ... rest unchanged
   }
   ```

3. Alternatively, add a `remove_proposal` endpoint that lets a proposer withdraw their own proposal and receive a refund before a winning vote is cast.

---

### Proof of Concept

**Step-by-step:**

1. Participant A calls `propose_update` with a 500 KB contract binary, paying ~5 NEAR deposit. Entry `id=0` is stored.
2. Participant B calls `propose_update` with a different 500 KB binary, paying ~5 NEAR deposit. Entry `id=1` is stored.
3. Threshold participants vote for `id=1`. `vote_update` calls `do_update(&id=1, gas)`.
4. Inside `do_update`:
   - `self.entries.remove(&id=1)` — removes B's entry (winning).
   - `self.entries.clear()` — removes A's entry (no refund issued).
   - `self.vote_by_participant.clear()`.
5. A's 5 NEAR deposit is now permanently locked in the contract. No `remove_proposal` endpoint exists. `UpdateEntry` for `id=0` is gone with no record of A's `AccountId`.

**Relevant code path:** [5](#0-4) [6](#0-5) 

The unit test `test_proposed_updates_do_update_clears_all_state` confirms that all entries are wiped on `do_update`, with no refund logic present: [7](#0-6)

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

**File:** crates/contract/src/lib.rs (L1383-1385)
```rust
        let Some(_promise) = self.proposed_updates.do_update(&id, update_gas_deposit) else {
            return Err(InvalidParameters::UpdateNotFound.into());
        };
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

**File:** crates/contract/README.md (L51-51)
```markdown
Participants can propose and vote on contract updates (code or configuration changes). When an update receives sufficient votes and is executed (via the `vote_update` endpoint which calls `do_update` internally), all pending update proposals and votes are cleared as they are no longer be valid after the contract migration. The update ID counter is preserved across migrations as part of the contract state to avoid race conditions where multiple participants might propose updates with colliding IDs immediately after an upgrade.
```
