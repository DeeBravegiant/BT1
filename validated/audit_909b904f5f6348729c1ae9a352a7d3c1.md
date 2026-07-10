### Title
Attached NEAR Deposit Silently Swallowed in `submit_participant_info` Re-submission Path — (`File: crates/contract/src/lib.rs`)

### Summary
`submit_participant_info` is marked `#[payable]`, but when an **existing participant** re-submits their attestation (not a new insertion), the entire deposit-check-and-refund block is skipped. Any NEAR tokens attached to such a call are permanently locked in the contract with no refund path.

### Finding Description

`submit_participant_info` is `#[payable]` and contains a conditional refund block gated on `attestation_storage_must_be_paid_by_caller`: [1](#0-0) 

```rust
let caller_is_not_participant = self.voter_account().is_err();
let is_new_attestation = matches!(
    attestation_insertion_result,
    ParticipantInsertion::NewlyInsertedParticipant
);

let attestation_storage_must_be_paid_by_caller =
    is_new_attestation || caller_is_not_participant;

if attestation_storage_must_be_paid_by_caller {
    // ... cost check and refund ...
}
```

When an **existing participant** (i.e., `caller_is_not_participant == false`) re-submits their attestation (i.e., `is_new_attestation == false`), the condition evaluates to `false` and the entire block — including the refund — is bypassed. Any NEAR tokens attached to the call are silently accepted by the contract and never returned. [2](#0-1) 

The function is unconditionally `#[payable]`, so the NEAR runtime will accept any attached deposit regardless of the execution path taken. [3](#0-2) 

The refund `Promise::new(account_id).transfer(diff).detach()` only executes inside the guarded block, so the re-submission path has no refund at all.

### Impact Explanation

Any NEAR tokens accidentally attached by an existing participant during a re-submission are permanently locked in the contract balance. There is no withdrawal mechanism for arbitrary contract-balance surpluses. This breaks the accounting invariant that callers receive back any deposit not consumed by storage costs. Impact: **Medium** — balance/accounting invariant violation causing permanent loss of user funds.

### Likelihood Explanation

Likelihood is **Low**. Callers are MPC node operators (sophisticated actors), but the function is callable by any existing participant at any time (e.g., to refresh an expiring TEE quote). Tooling or scripts that attach a small deposit "just in case" (a common defensive pattern in NEAR dApp development) would silently lose those funds on every re-submission.

### Recommendation

Either:
1. Add an unconditional refund of any attached deposit when `!attestation_storage_must_be_paid_by_caller`, mirroring the pattern already used in `require_deposit` and `propose_update`; or
2. Remove `#[payable]` from `submit_participant_info` and instead require callers to attach a deposit only when storage is actually needed (splitting into two entry points or using a pre-flight view call).

The simplest fix is to add an `else` branch:

```rust
} else {
    // Re-submission by existing participant: no storage cost, refund everything
    let attached = env::attached_deposit();
    if attached > NearToken::from_yoctonear(0) {
        Promise::new(account_id).transfer(attached).detach();
    }
}
```

### Proof of Concept

1. Participant `alice.near` is already registered (her attestation exists in `tee_state`).
2. Alice's TEE quote is approaching expiry; she calls `submit_participant_info` again with a fresh quote.
3. Alice's tooling attaches `1 NEAR` as a precautionary deposit.
4. Inside the function: `is_new_attestation = false` (update, not new), `caller_is_not_participant = false` (she is a participant).
5. `attestation_storage_must_be_paid_by_caller = false || false = false`.
6. The `if` block is skipped; no refund promise is created.
7. Alice's `1 NEAR` is permanently absorbed into the contract balance. [4](#0-3)

### Citations

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
