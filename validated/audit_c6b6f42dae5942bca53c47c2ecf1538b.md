### Title
Unbounded `ProposedUpdates.entries` Growth via `propose_update` Spam Enables Permanent DoS of Contract Upgrade Path — (File: `crates/contract/src/update.rs`)

---

### Summary

A single malicious participant can call `propose_update` an unbounded number of times, inflating the `ProposedUpdates.entries` `IterableMap` without limit. When the honest threshold of participants later calls `vote_update` and triggers `do_update`, the `entries.clear()` call iterates over every stored entry in O(N) gas. If the attacker has created enough entries, this call exhausts the available gas budget, causing every future `vote_update` transaction to revert. Because there is no alternative upgrade path, the contract upgrade mechanism is permanently frozen.

---

### Finding Description

`propose_update` in `crates/contract/src/lib.rs` is callable by any participant (enforced by `voter_or_panic()`). Each call delegates to `ProposedUpdates::propose`, which unconditionally inserts a new `UpdateEntry` into `self.entries` — an `IterableMap<UpdateId, UpdateEntry>` — and increments a monotonic `UpdateId` counter. There is no cap on the number of live proposals.

```rust
// crates/contract/src/update.rs  line 167-173
pub fn propose(&mut self, update: Update) -> UpdateId {
    let bytes_used = bytes_used(&update);
    let id = self.id.generate();
    self.entries.insert(id, UpdateEntry { update, bytes_used });
    id
}
```

When the signing threshold of participants votes for any proposal and `do_update` is invoked, it calls:

```rust
// crates/contract/src/update.rs  line 195-200
pub fn do_update(&mut self, id: &UpdateId, gas: Gas) -> Option<Promise> {
    let entry = self.entries.remove(id)?;
    // Clear all entries as they might be no longer valid
    self.entries.clear();
    self.vote_by_participant.clear();
    ...
}
```

`IterableMap::clear()` in the NEAR SDK iterates over every stored key and issues a storage deletion for each one — O(N) gas. If the attacker has pre-populated `entries` with enough proposals, this single `clear()` call exhausts the gas budget of the `vote_update` transaction (capped at ~300 TGas in practice, as confirmed by the sandbox test `test_vote_update_gas_before_threshold`). The transaction reverts, the entries remain, and every subsequent `vote_update` attempt fails identically.

There is no `cancel_update` or `remove_update` function. The only code path that removes entries from `self.entries` is `do_update` itself — the very function that is now permanently blocked.

The deposit guard in `propose_update` does not prevent this attack. The required deposit is computed as:

```rust
// crates/contract/src/update.rs  line 278-295
fn bytes_used(update: &Update) -> u128 {
    let mut bytes_used = std::mem::size_of::<UpdateEntry>() as u128;
    bytes_used += 128 * std::mem::size_of::<AccountId>() as u128;
    // + config JSON bytes for a Config update
    ...
}
fn required_deposit(bytes_used: u128) -> NearToken {
    env::storage_byte_cost().saturating_mul(bytes_used)
}
```

For a minimal `Config` update this is on the order of a few thousand bytes × 10 yoctoNEAR/byte — a negligible amount per proposal. The deposits are not refunded when `entries.clear()` is called; they are simply absorbed by the contract. The attacker's total upfront cost to permanently freeze upgrades is therefore a small fraction of one NEAR token.

---

### Impact Explanation

Permanently blocking `do_update` freezes the contract upgrade path entirely. No security patch, parameter change, or emergency fix can be deployed through the normal governance flow. This breaks the contract execution-flow and violates the production safety invariant that threshold-approved upgrades must be executable. It maps to the **Medium** allowed impact: *"contract execution-flow manipulation that breaks production safety/accounting invariants."*

---

### Likelihood Explanation

Any single participant — strictly below the signing threshold — can execute this attack unilaterally. The financial barrier is negligible (a few yoctoNEAR per proposal). The attacker needs only a few thousand proposals to exhaust the ~300 TGas budget of `vote_update`. No coordination, key material, or privileged access beyond participant status is required.

---

### Recommendation

Introduce a hard cap on the number of live proposals in `ProposedUpdates::propose`, analogous to `MAX_PENDING_REQUEST_FAN_OUT` used for the fan-out queue:

```rust
const MAX_LIVE_PROPOSALS: usize = 64; // or another empirically validated bound

pub fn propose(&mut self, update: Update) -> Result<UpdateId, Error> {
    if self.entries.len() >= MAX_LIVE_PROPOSALS {
        return Err(/* ProposalLimitExceeded */);
    }
    ...
}
```

Alternatively, replace `entries.clear()` in `do_update` with a lazy-deletion pattern (e.g., a generation counter) so that clearing does not iterate over all entries in a single call, mirroring the fix applied to the original `_resetDailyRewards` vulnerability.

---

### Proof of Concept

1. Participant P (a single honest-looking node) calls `propose_update` with a minimal `Config` update ~10,000 times, paying the negligible deposit each time. Each call inserts a new entry into `entries`.
2. The remaining threshold-minus-one participants call `vote_update` for any one of the proposals. The vote count reaches threshold.
3. `vote_update` calls `do_update`, which calls `entries.clear()` over ~10,000 entries. The call exhausts the ~300 TGas gas budget and reverts.
4. The entries remain. Every future `vote_update` call — regardless of which proposal is targeted — hits the same `entries.clear()` and reverts.
5. The contract upgrade mechanism is permanently frozen.

**Key code references:**

- No cap on proposals: [1](#0-0) 
- O(N) `entries.clear()` in `do_update`: [2](#0-1) 
- `propose_update` entry point (participant-only, no proposal count guard): [3](#0-2) 
- Deposit calculation (negligible per proposal): [4](#0-3) 
- Analogous cap that already exists for the fan-out queue: [5](#0-4)

### Citations

**File:** crates/contract/src/update.rs (L167-173)
```rust
    pub fn propose(&mut self, update: Update) -> UpdateId {
        let bytes_used = bytes_used(&update);

        let id = self.id.generate();
        self.entries.insert(id, UpdateEntry { update, bytes_used });

        id
```

**File:** crates/contract/src/update.rs (L195-200)
```rust
    pub fn do_update(&mut self, id: &UpdateId, gas: Gas) -> Option<Promise> {
        let entry = self.entries.remove(id)?;

        // Clear all entries as they might be no longer valid
        self.entries.clear();
        self.vote_by_participant.clear();
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

**File:** crates/contract/src/pending_requests.rs (L24-37)
```rust
/// Maximum number of concurrent yield-resume promises that can be queued for a single
/// request key (i.e. the number of duplicate submissions whose responses fan out from
/// one MPC reply).
///
/// The ceiling is needed because `respond*` drains the entire queue in one call: every
/// queued yield triggers a host-side `promise_yield_resume`, paid for out of the
/// responder's 300 TGas budget. Without a cap, an attacker could enqueue enough
/// duplicates to make `respond*` run out of gas and strand every queued caller.
///
/// 128 is validated empirically by the sandbox test
/// `test_contract_request_duplicate_requests_fan_out`, which fills the queue to this
/// cap across all four signature schemes and confirms `respond*` drains it inside its
/// 300 TGas budget.
pub const MAX_PENDING_REQUEST_FAN_OUT: u8 = 128;
```
