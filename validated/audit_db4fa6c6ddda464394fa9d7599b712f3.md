### Title
Attached NEAR Deposit Permanently Locked When Existing Participant Re-submits Attestation - (File: `crates/contract/src/lib.rs`)

### Summary

`submit_participant_info` is marked `#[payable]` but contains a conditional deposit-handling block that is entirely skipped when the caller is an already-registered participant performing a re-submission. Any NEAR tokens attached to such a call are silently absorbed by the contract with no refund path, permanently locking the funds.

### Finding Description

`submit_participant_info` computes a boolean gate to decide whether to charge and refund the caller:

```rust
let attestation_storage_must_be_paid_by_caller =
    is_new_attestation || caller_is_not_participant;

if attestation_storage_must_be_paid_by_caller {
    // ... charge for storage, refund excess ...
}
``` [1](#0-0) 

When both conditions are false — the caller **is** an existing participant **and** the attestation is **not** new (i.e., a re-submission / TEE-upgrade refresh by an already-registered node) — the entire deposit-handling block is bypassed. There is no `else` branch to refund the attached deposit. Because the function is `#[payable]`, the NEAR runtime accepts any deposit amount; the contract simply keeps it. [2](#0-1) 

The inline comment explains the *storage-byte* asymmetry (not refunding freed bytes), but says nothing about the attached-deposit case, confirming the omission is unintentional: [3](#0-2) 

### Impact Explanation

Any NEAR tokens attached to a re-submission call by an existing participant are permanently locked in the MPC contract. There is no `withdraw` function or recovery path. This breaks the production accounting invariant that `#[payable]` functions must either consume or refund attached deposits, and directly maps to the **Medium** allowed impact: *"Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants."*

### Likelihood Explanation

MPC nodes re-submit attestations during TEE software upgrades (the `tee_upgrade_deadline_duration` config field exists precisely for this). A node operator who attaches even 1 yoctoNEAR (e.g., following the same pattern as `sign` or `request_app_private_key` calls, both of which require a deposit) during a re-submission will lose those funds. The trigger is a normal, expected operational action, not an exotic edge case.

### Recommendation

Add an `else` branch (or move the refund outside the conditional) to return any attached deposit when storage payment is not required:

```rust
} else {
    let attached = env::attached_deposit();
    if attached > NearToken::from_yoctonear(0) {
        Promise::new(account_id).transfer(attached).detach();
    }
}
```

Alternatively, remove `#[payable]` from `submit_participant_info` entirely if no deposit is ever needed for re-submissions by existing participants, which would cause the NEAR runtime to reject any non-zero deposit at the protocol level.

### Proof of Concept

1. Node A is already a registered participant (`voter_account()` returns `Ok`).
2. A TEE software upgrade occurs; Node A calls `submit_participant_info` with a fresh attestation quote and accidentally attaches `1_000_000 yoctoNEAR`.
3. `add_participant` returns `ParticipantInsertion::UpdatedExistingParticipant` (not `NewlyInsertedParticipant`), so `is_new_attestation = false`.
4. `caller_is_not_participant = false` because `voter_account()` succeeds.
5. `attestation_storage_must_be_paid_by_caller = false || false = false`.
6. The `if` block is skipped entirely — no refund is issued.
7. The `1_000_000 yoctoNEAR` is permanently locked in the contract. [4](#0-3)

### Citations

**File:** crates/contract/src/lib.rs (L758-758)
```rust
    #[payable]
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
