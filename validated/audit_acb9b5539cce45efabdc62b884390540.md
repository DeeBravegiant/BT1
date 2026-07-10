### Title
Silent Deposit Absorption in `submit_participant_info` Re-Submission Path - (File: `crates/contract/src/lib.rs`)

---

### Summary

`submit_participant_info` is marked `#[payable]` and accepts attached NEAR deposits, but the deposit-refund logic is gated behind a conditional block that is **skipped entirely** when an existing participant re-submits their attestation. Any deposit attached in that code path is silently absorbed by the contract with no refund and no error.

---

### Finding Description

In `crates/contract/src/lib.rs`, `submit_participant_info` is declared `#[payable]`, signalling to callers that it accepts attached NEAR: [1](#0-0) 

After the attestation is inserted, the function computes a boolean gate: [2](#0-1) 

The entire deposit-handling block — which reads `env::attached_deposit()`, checks sufficiency, and schedules a refund — only executes when `attestation_storage_must_be_paid_by_caller` is `true`: [3](#0-2) 

When the condition is `false` (caller is an existing participant **and** the attestation is a re-submission, not a new entry), the function returns `Ok(())` without ever reading or refunding the attached deposit: [4](#0-3) 

The re-submission path is the **normal operational path** for existing participants. MPC nodes periodically re-submit their TEE attestations (e.g., after a TEE upgrade or on the hourly resubmission cycle described in the design docs). The test helper `submit_attestation` always attaches `NearToken::from_near(1)` regardless of whether the call is a first submission or a re-submission: [5](#0-4) 

Because the function is `#[payable]` with no documentation warning that deposits are non-refundable in the re-submission case, callers have no on-chain signal that their deposit will be silently consumed. The contract's own test scaffolding demonstrates this pattern — it always attaches 1 NEAR — making accidental deposit loss a realistic operational outcome.

---

### Impact Explanation

This matches the **Medium** allowed impact: *"Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration."*

Any NEAR attached to a re-submission call by an existing participant is permanently transferred to the contract's balance with no accounting entry and no refund path. The contract's balance grows by the leaked amount; the caller's balance shrinks by the same amount. This breaks the invariant — enforced everywhere else in the contract (e.g., `require_deposit`, `propose_update`) — that excess deposits are always refunded to the caller.

---

### Likelihood Explanation

Existing participants re-submit attestations on a regular schedule (hourly, or after TEE upgrades). The `#[payable]` annotation gives no indication that the deposit policy differs between first-submission and re-submission. The test helper always attaches 1 NEAR unconditionally, which is the pattern node operators and integrators will follow. Every periodic re-submission by an existing participant that attaches any non-zero deposit silently loses those funds.

---

### Recommendation

Add an unconditional excess-deposit refund at the end of `submit_participant_info`, outside the `if attestation_storage_must_be_paid_by_caller` block. When storage cost is zero (re-submission by an existing participant), the entire attached deposit is excess and must be returned:

```rust
// After the conditional storage-charge block:
if !attestation_storage_must_be_paid_by_caller {
    let attached = env::attached_deposit();
    if attached > NearToken::from_yoctonear(0) {
        Promise::new(account_id).transfer(attached).detach();
    }
}
```

Alternatively, restructure the deposit logic so that `env::attached_deposit()` is always read and any amount above the computed `cost` (which is 0 in the re-submission case) is always refunded, mirroring the pattern used in `require_deposit` and `propose_update`.

---

### Proof of Concept

1. Deploy the MPC contract and initialize it in `Running` state with participant `alice.near`.
2. `alice.near` calls `submit_participant_info` (first submission) with `attached_deposit = 1 NEAR`. Storage cost is charged; excess is refunded. ✓
3. `alice.near` calls `submit_participant_info` again (re-submission, same TLS key) with `attached_deposit = 1 NEAR`.
   - `is_new_attestation` = `false` (entry already exists → `ParticipantInsertion::UpdatedParticipant`)
   - `caller_is_not_participant` = `false` (`alice.near` is a current participant)
   - `attestation_storage_must_be_paid_by_caller` = `false`
   - The deposit block is skipped entirely.
   - `Ok(())` is returned.
4. `alice.near`'s balance decreases by 1 NEAR (plus gas). The contract's balance increases by 1 NEAR. No refund is ever scheduled.

The test helper at line 3996 confirms this is the expected call pattern — `NearToken::from_near(1)` is attached unconditionally for every `submit_participant_info` call, including re-submissions. [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L756-760)
```rust
    /// (Prospective) Participants can submit their tee participant information through this
    /// endpoint.
    #[payable]
    #[handle_result]
    pub fn submit_participant_info(
```

**File:** crates/contract/src/lib.rs (L817-852)
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
    }
```

**File:** crates/contract/src/lib.rs (L3993-4001)
```rust
        let participant_context = VMContextBuilder::new()
            .signer_account_id(account_id.clone())
            .predecessor_account_id(account_id.clone())
            .attached_deposit(NearToken::from_near(1))
            .build();
        testing_env!(participant_context);

        contract.submit_participant_info(Attestation::Mock(attestation), dto_public_key)
    }
```
