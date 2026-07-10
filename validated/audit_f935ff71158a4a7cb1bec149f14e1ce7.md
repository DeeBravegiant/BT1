### Title
Unrefunded Attached Deposit in `submit_participant_info` When Existing Participant Re-submits Attestation - (File: crates/contract/src/lib.rs)

### Summary
`submit_participant_info` is marked `#[payable]` and accepts attached NEAR deposits, but when an existing participant re-submits their TEE attestation (producing `ParticipantInsertion::UpdatedExistingParticipant`), the entire deposit-handling block is skipped. Any NEAR attached to such a call is permanently absorbed by the contract with no refund.

### Finding Description
In `submit_participant_info`, after `add_participant` returns, the code computes a guard:

```rust
let attestation_storage_must_be_paid_by_caller =
    is_new_attestation || caller_is_not_participant;

if attestation_storage_must_be_paid_by_caller {
    // ... check deposit >= cost, refund excess ...
}
``` [1](#0-0) 

When `add_participant` returns `ParticipantInsertion::UpdatedExistingParticipant` (an existing TLS-key entry is overwritten by the same account) **and** the caller is already a protocol participant, both conditions are `false`, so `attestation_storage_must_be_paid_by_caller` is `false`. The `if` block is never entered, `env::attached_deposit()` is never read, and any NEAR sent with the call is silently kept by the contract.

`ParticipantInsertion::UpdatedExistingParticipant` is returned whenever `stored_attestations.insert` finds a pre-existing entry for the same TLS key: [2](#0-1) 

The function is `#[payable]`, so the NEAR runtime does not reject calls that carry a deposit; it simply forwards the attached value to the contract account. [3](#0-2) 

By contrast, every other payable method in the contract (`require_deposit`, `propose_update`, `submit_participant_info`'s own `if` branch) explicitly refunds excess: [4](#0-3) [5](#0-4) 

### Impact Explanation
Any NEAR attached to a re-submission call by an existing participant is permanently locked in the contract with no recovery path. This breaks the production accounting invariant that `#[payable]` methods must refund excess deposits, and constitutes a direct, permanent loss of funds controlled by the MPC participant. The contract itself is the beneficiary, not an external attacker, but the caller's funds are unrecoverable.

**Impact: Medium** — balance/accounting invariant broken; participant funds permanently lost without relying on DoS or operator misconfiguration.

### Likelihood Explanation
MPC nodes periodically re-submit their TEE attestations to keep them fresh (attestations expire). The node software is automated and may or may not attach a deposit. Because the function is `#[payable]`, a node operator who follows the same pattern as the initial submission (attaching a small deposit to cover potential storage) will silently lose that deposit on every renewal. The scenario is reachable by any existing participant calling `submit_participant_info` with any non-zero `attached_deposit`.

### Recommendation
Add an unconditional refund of any attached deposit when `attestation_storage_must_be_paid_by_caller` is `false`, mirroring the pattern already used in `require_deposit`:

```rust
} else {
    // No storage cost for existing-participant re-submissions,
    // but still refund any deposit the caller accidentally attached.
    let attached = env::attached_deposit();
    if attached > NearToken::from_yoctonear(0) {
        Promise::new(account_id).transfer(attached).detach();
    }
}
```

Alternatively, reject calls with a non-zero deposit when no storage payment is required, consistent with the "revert on unexpected value" recommendation from the reference report.

### Proof of Concept

1. Participant `alice.near` calls `submit_participant_info` for the first time → `ParticipantInsertion::NewlyInsertedParticipant`, deposit is checked and excess refunded correctly.
2. Alice's attestation nears expiry. She calls `submit_participant_info` again with `attached_deposit = 1_000_000_000_000_000_000_000_000` yoctoNEAR (1 NEAR) to be safe.
3. `add_participant` finds the existing TLS-key entry and returns `ParticipantInsertion::UpdatedExistingParticipant`.
4. `caller_is_not_participant` is `false` (Alice is a current participant).
5. `attestation_storage_must_be_paid_by_caller` = `false || false` = `false`.
6. The `if` block is skipped entirely; `env::attached_deposit()` is never called.
7. Alice's 1 NEAR is permanently credited to the contract account with no refund promise issued. [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L134-138)
```rust
        Some(diff) => {
            if diff > NearToken::from_yoctonear(0) {
                log!("refund excess deposit {diff} to {predecessor}");
                Promise::new(predecessor.clone()).transfer(diff).detach();
            }
```

**File:** crates/contract/src/lib.rs (L758-760)
```rust
    #[payable]
    #[handle_result]
    pub fn submit_participant_info(
```

**File:** crates/contract/src/lib.rs (L817-849)
```rust
        let caller_is_not_participant = self.voter_account().is_err();
        let is_new_attestation = matches!(
            attestation_insertion_result,
            ParticipantInsertion::NewlyInsertedParticipant
        );

        let attestation_storage_must_be_paid_by_caller =
            is_new_attestation || caller_is_not_participant;

        if attestation_storage_must_be_paid_by_caller {
            // `saturating_sub`: if a re-submission shrinks the entry, charge nothing
            // rather than underflow. Intentional asymmetry: we do not refund freed bytes
            // either — the caller already paid for the larger entry, and we'd rather
            // accept that asymmetry than open a refund path for payload-shrinking games.
            let storage_used = env::storage_usage().saturating_sub(initial_storage);
            let cost = env::storage_byte_cost().saturating_mul(storage_used as u128);
            let attached = env::attached_deposit();

            if attached < cost {
                return Err(InvalidParameters::InsufficientDeposit {
                    attached: attached.as_yoctonear(),
                    required: cost.as_yoctonear(),
                }
                .into());
            }

            // Refund the difference if the proposer attached more than required
            if let Some(diff) = attached.checked_sub(cost)
                && diff > NearToken::from_yoctonear(0)
            {
                Promise::new(account_id).transfer(diff).detach();
            }
        }
```

**File:** crates/contract/src/lib.rs (L1327-1331)
```rust
        if let Some(diff) = attached.checked_sub(required)
            && diff > NearToken::from_yoctonear(0)
        {
            Promise::new(proposer).transfer(diff).detach();
        }
```

**File:** crates/contract/src/tee/tee_state.rs (L199-202)
```rust
        Ok(match insertion {
            Some(_previous_attestation) => ParticipantInsertion::UpdatedExistingParticipant,
            None => ParticipantInsertion::NewlyInsertedParticipant,
        })
```
