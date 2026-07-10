### Title
Excess Attached Deposit Permanently Locked in Contract on Attestation Re-submission - (File: crates/contract/src/lib.rs)

### Summary

`submit_participant_info` is marked `#[payable]` and accepts any attached NEAR deposit, but the deposit-check-and-refund block is only executed when `attestation_storage_must_be_paid_by_caller` is `true`. When an existing participant re-submits their attestation (an update, not a new insertion), this condition is `false`, the block is skipped entirely, and any attached deposit is silently absorbed by the contract with no refund path.

### Finding Description

In `crates/contract/src/lib.rs`, `submit_participant_info` computes:

```rust
let caller_is_not_participant = self.voter_account().is_err();
let is_new_attestation = matches!(
    attestation_insertion_result,
    ParticipantInsertion::NewlyInsertedParticipant
);
let attestation_storage_must_be_paid_by_caller =
    is_new_attestation || caller_is_not_participant;

if attestation_storage_must_be_paid_by_caller {
    // ... deposit sufficiency check ...
    // Refund the difference if the proposer attached more than required
    if let Some(diff) = attached.checked_sub(cost)
        && diff > NearToken::from_yoctonear(0)
    {
        Promise::new(account_id).transfer(diff).detach();
    }
}
// ← no else branch: when condition is false, deposit is never read or refunded
Ok(())
```

When `attestation_storage_must_be_paid_by_caller` is `false` — i.e., the caller is already a participant **and** the attestation is an update rather than a new insertion — the entire block is bypassed. The function is `#[payable]`, so the NEAR runtime happily accepts whatever deposit the caller attached. That deposit is transferred to the contract's account and there is no code path that ever returns it.

Compare this to the analogous functions in the same file: `require_deposit` (used by `sign`, `request_app_private_key`, `verify_foreign_transaction`) and `propose_update` both unconditionally refund any excess above the required minimum. `submit_participant_info` does not. [1](#0-0) 

The refund logic in `require_deposit` for comparison: [2](#0-1) 

### Impact Explanation

Any NEAR tokens attached to a re-submission call by an existing participant are permanently locked inside the contract. The contract has no withdrawal or sweep mechanism for such stranded balances. This is a direct, irrecoverable loss of funds for the caller. The impact maps to the **Medium** allowed scope: *balance/accounting invariant broken without relying on network-level DoS or operator misconfiguration*.

### Likelihood Explanation

**Low.** The production node (`periodic_attestation_submission`) is documented to attach `0` deposit for re-submissions, and the operator guide explicitly states "the node attaches 0" for periodic re-attestation. However, the operator guide also instructs first-time joiners to call `submit_participant_info` manually with `--deposit`. An operator who has already been admitted as a participant and then manually re-calls the function with a deposit (e.g., after an image-hash rotation forces a re-submission, or due to confusion about their current state) will silently lose those tokens. The function is also publicly callable by any account, so any external caller who is already a participant and attaches a deposit loses it. [3](#0-2) 

### Recommendation

Remove the conditional guard around the refund. The deposit should always be read and any excess above the actual storage cost should always be returned to the caller, regardless of whether storage payment is required for this particular call. When `attestation_storage_must_be_paid_by_caller` is `false`, the required cost is zero, so the entire attached deposit should be refunded:

```rust
let storage_used = env::storage_usage().saturating_sub(initial_storage);
let cost = if attestation_storage_must_be_paid_by_caller {
    env::storage_byte_cost().saturating_mul(storage_used as u128)
} else {
    NearToken::from_yoctonear(0)
};
let attached = env::attached_deposit();
if attached < cost {
    return Err(InvalidParameters::InsufficientDeposit { ... }.into());
}
if let Some(diff) = attached.checked_sub(cost)
    && diff > NearToken::from_yoctonear(0)
{
    Promise::new(account_id).transfer(diff).detach();
}
```

This mirrors the pattern already used in `require_deposit` and `propose_update`. [4](#0-3) 

### Proof of Concept

1. Alice is an existing MPC participant (her account passes `self.voter_account().is_ok()`).
2. Alice's attestation is already stored (`add_participant` returns `ParticipantInsertion::UpdatedParticipant`, so `is_new_attestation = false`).
3. Alice calls `submit_participant_info` with `attached_deposit = 10 NEAR` (e.g., following the operator guide's instruction to attach a deposit, not realising she is already registered).
4. `attestation_storage_must_be_paid_by_caller = false || false = false`.
5. The `if` block is skipped. `env::attached_deposit()` is never read. No `Promise::transfer` is scheduled.
6. The call returns `Ok(())`. Alice's 10 NEAR are now in the contract's account with no recovery path. [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L122-141)
```rust
fn require_deposit(minimum_deposit: NearToken, predecessor: &AccountId) {
    let deposit = env::attached_deposit();
    match deposit.checked_sub(minimum_deposit) {
        None => {
            env::panic_str(
                &InvalidParameters::InsufficientDeposit {
                    attached: deposit.as_yoctonear(),
                    required: minimum_deposit.as_yoctonear(),
                }
                .to_string(),
            );
        }
        Some(diff) => {
            if diff > NearToken::from_yoctonear(0) {
                log!("refund excess deposit {diff} to {predecessor}");
                Promise::new(predecessor.clone()).transfer(diff).detach();
            }
        }
    }
}
```

**File:** crates/contract/src/lib.rs (L756-760)
```rust
    /// (Prospective) Participants can submit their tee participant information through this
    /// endpoint.
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

**File:** crates/contract/src/lib.rs (L1326-1331)
```rust
        // Refund the difference if the proposer attached more than required.
        if let Some(diff) = attached.checked_sub(required)
            && diff > NearToken::from_yoctonear(0)
        {
            Promise::new(proposer).transfer(diff).detach();
        }
```
