### Title
Deposit Not Refunded in `submit_participant_info` Re-Submission Path — (`File: crates/contract/src/lib.rs`)

### Summary

`submit_participant_info` is `#[payable]` and silently absorbs any attached NEAR deposit when an existing participant re-submits their attestation. The new-participant path and every other payable method in the contract refund excess deposits, but the re-submission branch skips the refund block entirely, permanently locking any attached NEAR in the contract.

### Finding Description

`submit_participant_info` computes a boolean flag `attestation_storage_must_be_paid_by_caller` that is `true` only when the caller is a new participant **or** a non-participant. When an existing participant updates their attestation (the normal periodic re-submission case), the flag is `false` and the entire deposit-accounting block is skipped: [1](#0-0) 

When `attestation_storage_must_be_paid_by_caller` is `false`:
- `env::attached_deposit()` is never read.
- No refund promise is scheduled.
- Any NEAR attached to the call is silently retained by the contract.

The function is `#[payable]` with no documentation warning that deposits are non-refundable in this branch, and no enforcement that callers must attach exactly zero.

Compare with every other payable method in the contract:

- `require_deposit` (called by `sign`, `request_app_private_key`, `verify_foreign_transaction`) always refunds excess above the 1-yoctonear minimum: [2](#0-1) 
- `propose_update` always refunds excess: [3](#0-2) 
- The **new-participant** branch of `submit_participant_info` itself refunds excess: [4](#0-3) 

The re-submission branch is the only payable code path that provides no refund.

### Impact Explanation

Any NEAR attached to a re-submission call by an existing participant is permanently locked in the contract. There is no sweep, withdraw, or admin-recovery method for arbitrary contract balances. The node's `periodic_attestation_submission` task re-submits on a 1-hour cadence; if the node code attaches any deposit (even 1 yoctonear) as a safety margin for the case where it cannot determine in advance whether the submission is new or an update, that deposit is lost on every re-submission cycle. Over time this constitutes a steady drain of node-operator funds with no recovery path.

This breaks the production accounting invariant that deposits are refunded when not consumed — the same invariant upheld by every other payable method in the contract.

**Impact: Medium** — balance/accounting invariant broken, NEAR permanently frozen in the contract, no recovery mechanism.

### Likelihood Explanation

The re-submission path is the **normal operating path** for every active participant. The node re-submits attestations every hour and on attestation-removal events. [5](#0-4) 

Whether the node attaches a deposit depends on the node implementation. Because the function is `#[payable]` and the node cannot always know ahead of time whether its submission will be treated as new or as an update (the distinction is made inside the contract after `add_participant` runs), a defensive implementation that always attaches a small deposit to cover the new-participant case will silently lose that deposit on every re-submission.

**Likelihood: Medium** — triggered on every periodic re-submission by any existing participant that attaches a non-zero deposit.

### Recommendation

Add an unconditional refund of any attached deposit when `attestation_storage_must_be_paid_by_caller` is `false`:

```rust
if attestation_storage_must_be_paid_by_caller {
    // existing storage-charge + refund logic
} else {
    // Refund the entire deposit — no storage cost for re-submissions by existing participants.
    let attached = env::attached_deposit();
    if attached > NearToken::from_yoctonear(0) {
        Promise::new(account_id).transfer(attached).detach();
    }
}
```

Alternatively, document clearly that callers must attach exactly zero for re-submissions and add an explicit check that panics if a non-zero deposit is attached in this branch, preventing silent loss.

### Proof of Concept

1. Alice is an existing participant (voter) with a stored attestation.
2. Alice calls `submit_participant_info` with a valid updated attestation and attaches `1_000_000_000_000_000_000_000_000` yoctonear (1 NEAR) as a deposit.
3. Inside the function: `caller_is_not_participant = false`, `is_new_attestation = false` (update, not new insertion), so `attestation_storage_must_be_paid_by_caller = false`.
4. The `if attestation_storage_must_be_paid_by_caller { ... }` block is skipped entirely.
5. The function returns `Ok(())`.
6. Alice's 1 NEAR is now held by the contract with no refund scheduled and no recovery path.
7. Alice's balance is 1 NEAR lower; the contract balance is 1 NEAR higher.
8. Repeating this every hour (the re-submission cadence) drains 1 NEAR per cycle from Alice's node account.

### Citations

**File:** crates/contract/src/lib.rs (L134-138)
```rust
        Some(diff) => {
            if diff > NearToken::from_yoctonear(0) {
                log!("refund excess deposit {diff} to {predecessor}");
                Promise::new(predecessor.clone()).transfer(diff).detach();
            }
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
