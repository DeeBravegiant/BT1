### Title
Deposits from Non-Winning `propose_update` Proposals Are Permanently Frozen in the Contract - (File: `crates/contract/src/update.rs`, `crates/contract/src/lib.rs`)

### Summary
When `vote_update` reaches threshold and executes an update via `do_update`, all competing update proposals are cleared from storage without refunding the NEAR deposits those proposers attached. Because `UpdateEntry` stores neither the proposer's `AccountId` nor the deposited amount, the contract has no record of who to refund or how much, making those deposits permanently irrecoverable without a contract upgrade.

### Finding Description
`propose_update` requires a deposit proportional to the storage cost of the proposed update: [1](#0-0) 

Only the *excess* above the required amount is immediately refunded to the proposer: [2](#0-1) 

The required deposit stays in the contract. However, `UpdateEntry` — the struct stored per proposal — contains no `proposer` account ID and no `attached_deposit` field: [3](#0-2) 

When `do_update` executes the winning proposal, it unconditionally clears **all** entries and votes: [4](#0-3) 

No refund is issued to any of the cleared proposers. The NEAR tokens they deposited remain in the contract balance with no on-chain path to recover them.

### Impact Explanation
This matches the **Medium** allowed impact: *"Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants."*

The deposit required for a contract-code update is:

```
bytes_used = size_of::<UpdateEntry>()
           + 128 * size_of::<AccountId>()   // assumed max votes
           + code.len()
``` [5](#0-4) 

A typical NEAR contract binary is 1–5 MB. At NEAR's storage cost of ~1 NEAR per 10 KB, a single competing proposal can lock 100–500 NEAR permanently. Multiple simultaneous proposals multiply the loss. The funds are irrecoverable without a contract upgrade, exactly mirroring the original report's conclusion about the `Wallet.refund` bug.

### Likelihood Explanation
This triggers in **normal protocol operation**, not an adversarial edge case. Whenever two or more participants independently propose different updates (e.g., one proposes a code upgrade, another proposes a config change), executing either one silently destroys the other's deposit. No collusion, no special privilege, and no network-level attack is required — only the ordinary threshold-vote flow.

### Recommendation
1. Add `proposer: AccountId` and `attached_deposit: NearToken` fields to `UpdateEntry`.
2. In `do_update`, before calling `self.entries.clear()`, iterate over all remaining entries and schedule `Promise::new(entry.proposer).transfer(entry.attached_deposit)` for each non-winning proposal.
3. Alternatively, track deposits in a separate `LookupMap<UpdateId, (AccountId, NearToken)>` and drain it on every `do_update` call.

### Proof of Concept

1. Participant A calls `propose_update` with a 200 NEAR deposit (large contract binary). The required deposit is retained; only excess is refunded.
2. Participant B calls `propose_update` with a 5 NEAR deposit (config change). Same accounting.
3. Threshold participants call `vote_update(B_id)`. `do_update` executes update B, then calls `self.entries.clear()` — removing A's entry with no refund.
4. Participant A's 200 NEAR is now permanently locked in the contract. `UpdateEntry` for A no longer exists; there is no on-chain record of the depositor or amount. Recovery requires a contract upgrade. [4](#0-3) [6](#0-5)

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
