### Title
Deposit Permanently Lost for Non-Executed Update Proposals When `do_update` Clears All Entries — (`File: crates/contract/src/update.rs`)

---

### Summary

`propose_update` collects a storage-staking deposit from each proposer but neither records the proposer's `AccountId` nor the deposit amount inside `UpdateEntry`. When any update reaches threshold and `do_update` is called, it unconditionally clears **all** pending entries without issuing refunds. Every participant who proposed a competing update permanently loses their storage deposit.

---

### Finding Description

`propose_update` computes a required deposit via `ProposedUpdates::required_deposit`, which calls `env::storage_byte_cost()` multiplied by the estimated bytes the entry will occupy. The exact amount paid is immediately consumed as storage staking; only the excess above `required` is refunded to the proposer at call time. [1](#0-0) 

The `UpdateEntry` struct that is persisted stores only the update payload and a `bytes_used` estimate — **no proposer `AccountId`, no deposit amount**: [2](#0-1) 

`ProposedUpdates` has no `proposer_by_update_id` map either: [3](#0-2) 

When threshold votes are reached, `vote_update` calls `do_update`, which removes the winning entry and then calls `self.entries.clear()` and `self.vote_by_participant.clear()` — discarding every competing proposal with no refund logic: [4](#0-3) 

The `required_deposit` function itself uses the live `env::storage_byte_cost()` at proposal time: [5](#0-4) 

Because neither the proposer identity nor the deposit amount is stored, there is no mechanism to return the storage-staking deposit to proposers of non-executed proposals when `do_update` sweeps the entries map.

---

### Impact Explanation

Every participant who proposed a competing update loses their full storage-staking deposit permanently. For a large contract binary the deposit can be substantial — the test suite uses `CURRENT_CONTRACT_DEPLOY_DEPOSIT = NearToken::from_millinear(17000)` (17 NEAR) as the expected deposit for a current-sized binary: [6](#0-5) 

This breaks the production accounting invariant that storage-staking deposits are returned when the corresponding storage is freed. The freed storage bytes are reclaimed by the contract's balance rather than returned to the proposers. Impact class: **Medium** — balance/accounting invariant break causing permanent fund loss for participants, without requiring network-level DoS or operator misconfiguration.

---

### Likelihood Explanation

In any realistic multi-participant deployment, multiple participants may independently propose different updates (e.g., different contract binaries or config changes). The moment any one proposal reaches threshold and `do_update` fires, all competing proposals are silently cleared. This is a normal, expected operational event — not an edge case — making the deposit loss a routine occurrence whenever competing proposals exist.

---

### Recommendation

Store the proposer's `AccountId` and the exact deposit amount inside `UpdateEntry`:

```rust
pub(crate) struct UpdateEntry {
    pub(super) update: Update,
    pub(super) bytes_used: u128,
    pub(super) proposer: AccountId,   // add
    pub(super) deposit: NearToken,    // add
}
```

In `do_update`, before calling `self.entries.clear()`, iterate over all remaining entries and schedule a `Promise::new(entry.proposer).transfer(entry.deposit)` for each one. This mirrors the pattern already used in `propose_update` for excess-deposit refunds and in `resolve_verification` / `on_attestation_verified` for attestation deposit refunds. [7](#0-6) 

---

### Proof of Concept

1. Participant A calls `propose_update` with a 2 MB contract binary, paying ~17 NEAR as storage deposit. The contract stores `UpdateEntry { update, bytes_used }` — no proposer or deposit recorded.
2. Participant B calls `propose_update` with a different binary, also paying ~17 NEAR.
3. Threshold participants vote for B's proposal. `vote_update` calls `do_update(&id_B, gas)`.
4. `do_update` removes entry B, then calls `self.entries.clear()` — entry A is deleted with no refund.
5. Participant A's 17 NEAR is permanently absorbed into the contract's balance. There is no code path that can recover it. [4](#0-3) [8](#0-7)

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

**File:** crates/contract/src/update.rs (L143-149)
```rust
#[near(serializers=[borsh ])]
#[derive(Debug)]
pub struct ProposedUpdates {
    pub(super) vote_by_participant: IterableMap<AccountId, UpdateId>,
    pub(super) entries: IterableMap<UpdateId, UpdateEntry>,
    pub(super) id: UpdateId,
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

**File:** crates/contract/src/update.rs (L297-299)
```rust
fn required_deposit(bytes_used: u128) -> NearToken {
    env::storage_byte_cost().saturating_mul(bytes_used)
}
```

**File:** crates/contract/tests/sandbox/utils/consts.rs (L46-46)
```rust
pub const CURRENT_CONTRACT_DEPLOY_DEPOSIT: NearToken = NearToken::from_millinear(17000);
```
