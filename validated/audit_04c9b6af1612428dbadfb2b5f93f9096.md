### Title
Deposit Permanently Locked in `submit_participant_info` When Existing Participant Re-Submits Attestation - (File: crates/contract/src/lib.rs)

### Summary

`submit_participant_info` is a `#[payable]` function that accepts any attached NEAR deposit, but only executes a refund path when `attestation_storage_must_be_paid_by_caller` is `true`. When an existing participant re-submits their attestation (not a new insertion), this flag is `false` and the entire attached deposit is silently absorbed into the contract with no refund, permanently locking the funds.

### Finding Description

The refund logic in `submit_participant_info` is gated behind a conditional that is only entered when the caller is either a new participant or a non-participant: [1](#0-0) 

```rust
let caller_is_not_participant = self.voter_account().is_err();
let is_new_attestation = matches!(
    attestation_insertion_result,
    ParticipantInsertion::NewlyInsertedParticipant
);

let attestation_storage_must_be_paid_by_caller =
    is_new_attestation || caller_is_not_participant;

if attestation_storage_must_be_paid_by_caller {
    // ... storage cost check ...
    // Refund the difference if the proposer attached more than required
    if let Some(diff) = attached.checked_sub(cost)
        && diff > NearToken::from_yoctonear(0)
    {
        Promise::new(account_id).transfer(diff).detach();
    }
}
```

When `attestation_storage_must_be_paid_by_caller` is `false` — i.e., when an **existing participant** performs a **re-submission** (not a new insertion) — the `if` block is skipped entirely. The function is still `#[payable]`, so any deposit attached by the caller is accepted by the runtime, but there is no code path that returns it. The deposit is permanently locked in the contract.

This is structurally identical to the M-02 pattern: a payable entry point consumes funds sent to it, but only partially uses them, with no mechanism to return the remainder to the sender.

The analog mapping:
- **M-02**: `uniswapV3SwapCallback` wraps only `_amountToSend` of `msg.value` into WETH; the remainder of `msg.value` is locked.
- **NEAR MPC**: `submit_participant_info` charges only the storage delta (zero, for a re-submission by an existing participant) but absorbs the entire `attached_deposit` with no refund.

The `require_deposit` helper used by `sign` and `request_app_private_key` always refunds excess: [2](#0-1) 

`submit_participant_info` does not call `require_deposit` and instead implements its own deposit handling that is missing the unconditional refund path.

### Impact Explanation

Any NEAR tokens attached to a `submit_participant_info` call by an existing participant performing a re-submission are permanently frozen inside the MPC contract. The contract accumulates these funds with no withdrawal mechanism. This breaks the production safety/accounting invariant that `#[payable]` functions must refund unused deposits. The contract's NEAR balance grows unboundedly relative to its actual storage obligations, and the locked funds are irrecoverable.

This matches the allowed impact: **Medium — Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants.**

### Likelihood Explanation

The `submit_participant_info` endpoint is called by MPC nodes on a periodic cadence (every hour, per the design documentation) and on attestation-removal events. Nodes re-submit their attestation regularly as part of normal operation. If the node implementation attaches any non-zero deposit (e.g., 1 yoctoNEAR, which is the standard minimum for payable NEAR functions), each re-submission permanently locks that amount. Over time, across all participants and all re-submissions, the locked amount accumulates. A Byzantine participant below the signing threshold can also deliberately attach a large deposit during re-submission to amplify the accounting discrepancy, though the primary harm is to their own funds.

### Recommendation

Add an unconditional refund of any excess deposit in the `else` branch (or after the `if` block) of `submit_participant_info`, mirroring the pattern used by `require_deposit`:

```rust
} else {
    // Caller is an existing participant doing a re-submission; no storage cost.
    // Refund the entire deposit if any was attached.
    let attached = env::attached_deposit();
    if attached > NearToken::from_yoctonear(0) {
        Promise::new(account_id).transfer(attached).detach();
    }
}
```

Alternatively, restructure the deposit handling to always compute the required cost and always refund the difference, regardless of the `attestation_storage_must_be_paid_by_caller` flag.

### Proof of Concept

1. Alice is an existing, attested participant (i.e., `self.voter_account()` returns `Ok`).
2. Alice's node re-submits her attestation (e.g., on the hourly cadence), attaching `1_000_000_000_000_000_000_000_000` yoctoNEAR (1 NEAR) as a deposit.
3. `add_participant` returns `ParticipantInsertion::UpdatedExistingParticipant` (not `NewlyInsertedParticipant`), so `is_new_attestation = false`.
4. `caller_is_not_participant = false` because Alice is a voter.
5. `attestation_storage_must_be_paid_by_caller = false || false = false`.
6. The `if attestation_storage_must_be_paid_by_caller` block is skipped.
7. The function returns `Ok(())`.
8. Alice's 1 NEAR deposit is now permanently locked in the MPC contract with no refund issued and no withdrawal path. [3](#0-2)

### Citations

**File:** crates/contract/src/lib.rs (L122-140)
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
