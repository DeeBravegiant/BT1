### Title
Attached Deposit Permanently Locked in `submit_participant_info` for Existing-Participant Re-submissions — (File: `crates/contract/src/lib.rs`)

---

### Summary
The `submit_participant_info` function is marked `#[payable]` and accepts NEAR token deposits. For new participants it correctly charges storage costs and refunds any excess. However, when an **existing participant re-submits their attestation** (e.g., for periodic TEE renewal), the entire deposit-handling block is skipped and any attached deposit is permanently locked in the contract with no recovery path.

---

### Finding Description
The deposit-handling logic is gated on a single boolean:

```rust
let attestation_storage_must_be_paid_by_caller =
    is_new_attestation || caller_is_not_participant;

if attestation_storage_must_be_paid_by_caller {
    // ... charge storage cost, refund excess ...
    Promise::new(account_id).transfer(diff).detach();
}
// ← no else branch; any deposit attached here is silently kept
``` [1](#0-0) 

When `is_new_attestation = false` **and** `caller_is_not_participant = false` (i.e., an existing participant updates their attestation), the condition evaluates to `false`. The function is `#[payable]`, so the NEAR runtime accepts whatever deposit the caller attaches, but the contract never issues a refund. The deposit is absorbed into the contract balance with no withdrawal mechanism. [2](#0-1) 

MPC nodes re-submit attestations periodically for TEE renewal. If a node attaches even 1 yoctoNEAR on a re-submission call, those tokens are permanently locked.

---

### Impact Explanation
**Medium — permanent freezing of participant-owned NEAR tokens inside the MPC contract.**

The contract's own accounting invariant — "excess deposits are always refunded" — is broken for the re-submission code path. Locked funds accumulate in the contract balance and cannot be recovered by the participant or by governance. This matches the allowed Medium impact: *"Balance … manipulation that breaks production safety/accounting invariants."*

---

### Likelihood Explanation
**Medium.** MPC nodes submit attestations periodically for TEE certificate renewal (the codebase references `periodic-attestation-submission` and `attestation-resubmission-interval`). The function is `#[payable]`, so any caller — including automated node software that defensively attaches a small deposit — will silently lose those tokens. No malicious intent is required; the bug triggers on any non-zero deposit during a re-submission by an existing participant.

---

### Recommendation
Add an unconditional refund of any attached deposit in the branch where `attestation_storage_must_be_paid_by_caller` is `false`:

```rust
} else {
    // Existing participant re-submission: no storage cost, refund any deposit
    let attached = env::attached_deposit();
    if attached > NearToken::from_yoctonear(0) {
        Promise::new(account_id).transfer(attached).detach();
    }
}
```

Alternatively, move the refund logic outside the conditional so it always executes for any deposit above the required cost (including zero cost).

---

### Proof of Concept

1. Deploy the MPC contract and initialize it with two participants.
2. As participant `alice`, call `submit_participant_info` with a valid attestation and `attached_deposit = 1_000_000 yoctoNEAR`. This is the **first** submission — `is_new_attestation = true`, so the deposit-handling block runs and the excess is refunded correctly.
3. As `alice` again (now an existing participant), call `submit_participant_info` a second time with an updated attestation and `attached_deposit = 1_000_000 yoctoNEAR`.
   - `is_new_attestation = false` (updating, not inserting)
   - `caller_is_not_participant = false` (alice is already a participant)
   - `attestation_storage_must_be_paid_by_caller = false`
   - The deposit-handling block is **skipped**.
4. Observe that `alice`'s account balance decreased by `1_000_000 yoctoNEAR` and the contract balance increased by the same amount. No refund promise is ever scheduled. The tokens are permanently locked. [3](#0-2)

### Citations

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
