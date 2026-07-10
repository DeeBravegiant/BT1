### Title
Deposit Silently Consumed Without Refund on Existing-Participant Attestation Re-submission - (File: crates/contract/src/lib.rs)

### Summary
`submit_participant_info` is `#[payable]` but contains a conditional refund branch that is skipped entirely when an existing participant updates their own attestation. Any NEAR tokens attached in that scenario are permanently locked in the contract with no recovery path.

### Finding Description
In `submit_participant_info`, after the attestation is stored, the contract computes a boolean gate:

```rust
let caller_is_not_participant = self.voter_account().is_err();
let is_new_attestation = matches!(
    attestation_insertion_result,
    ParticipantInsertion::NewlyInsertedParticipant
);

let attestation_storage_must_be_paid_by_caller =
    is_new_attestation || caller_is_not_participant;

if attestation_storage_must_be_paid_by_caller {
    // ... deposit check and refund logic
}
``` [1](#0-0) 

When the caller is an **existing participant** (`caller_is_not_participant == false`) performing an **attestation update** (`is_new_attestation == false`), the entire `if` block is skipped. The function returns `Ok(())` without ever reading `env::attached_deposit()` or issuing a refund. Because the function is `#[payable]`, the NEAR runtime accepts any deposit the caller attaches, and those tokens are credited to the contract balance with no mechanism to recover them.

The `add_participant` function in `tee_state.rs` confirms that re-submissions by the same account return `ParticipantInsertion::UpdatedExistingParticipant`, which is the exact path that triggers the silent deposit consumption: [2](#0-1) 

### Impact Explanation
Any NEAR tokens attached to a re-submission call by an existing participant are permanently locked inside the MPC contract. The contract has no withdrawal or sweep function for such stranded balances. This breaks the accounting invariant that deposits must either be consumed for storage or refunded — matching the **Medium** impact class: *balance or contract execution-flow manipulation that breaks production safety/accounting invariants*.

### Likelihood Explanation
The MPC node's `periodic_attestation_submission` task attaches 0 deposit for re-submissions in normal operation, so automated flows are unaffected. However, the documentation explicitly instructs operators to call `submit_participant_info` manually with `--deposit` for first-time joins. An operator who follows that instruction on a subsequent re-submission (e.g., after a key rotation or attestation renewal) will silently lose the attached NEAR. The function is `#[payable]` with no guard rejecting non-zero deposits in the update path, so the loss is silent and irreversible. [3](#0-2) 

### Recommendation
Add an unconditional excess-deposit refund at the end of `submit_participant_info`, outside the `if attestation_storage_must_be_paid_by_caller` block:

```rust
// Always refund any deposit not consumed by storage costs.
let attached = env::attached_deposit();
if attached > NearToken::from_yoctonear(0) {
    Promise::new(account_id).transfer(attached).detach();
}
```

Alternatively, restructure the logic so that the deposit is read once at the top, the storage cost is computed (zero for updates by existing participants), and the difference is always refunded.

### Proof of Concept
1. Deploy the MPC contract and register participant `alice.near` via `submit_participant_info` with the required storage deposit. `alice.near` is now a voter/participant.
2. As `alice.near`, call `submit_participant_info` again (attestation renewal) and attach `1 NEAR` as deposit.
3. `add_participant` returns `ParticipantInsertion::UpdatedExistingParticipant` → `is_new_attestation = false`.
4. `voter_account()` succeeds for `alice.near` → `caller_is_not_participant = false`.
5. `attestation_storage_must_be_paid_by_caller = false || false = false` → the entire deposit/refund block is skipped.
6. The function returns `Ok(())`. The `1 NEAR` is now in the contract balance with no recovery path. [4](#0-3)

### Citations

**File:** crates/contract/src/lib.rs (L756-760)
```rust
    /// (Prospective) Participants can submit their tee participant information through this
    /// endpoint.
    #[payable]
    #[handle_result]
    pub fn submit_participant_info(
```

**File:** crates/contract/src/lib.rs (L817-851)
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

        Ok(())
```

**File:** crates/contract/src/tee/tee_state.rs (L199-202)
```rust
        Ok(match insertion {
            Some(_previous_attestation) => ParticipantInsertion::UpdatedExistingParticipant,
            None => ParticipantInsertion::NewlyInsertedParticipant,
        })
```
