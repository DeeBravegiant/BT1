### Title
Unchecked Deposit Handling in `submit_participant_info` Permanently Locks Caller NEAR — (`File: crates/contract/src/lib.rs`)

---

### Summary

`submit_participant_info` is marked `#[payable]` and accepts NEAR deposits, but its deposit-refund logic is gated behind a condition that evaluates to `false` for existing participants re-submitting their attestation. When that branch is skipped, any NEAR attached to the call is silently consumed by the contract with no refund and no error — a direct accounting invariant break analogous to the unchecked ERC20 transfer in the reference report.

---

### Finding Description

In `crates/contract/src/lib.rs`, `submit_participant_info` computes a guard:

```rust
let caller_is_not_participant = self.voter_account().is_err();
let is_new_attestation = matches!(
    attestation_insertion_result,
    ParticipantInsertion::NewlyInsertedParticipant
);

let attestation_storage_must_be_paid_by_caller =
    is_new_attestation || caller_is_not_participant;

if attestation_storage_must_be_paid_by_caller {
    // deposit check + excess refund
    ...
    Promise::new(account_id).transfer(diff).detach();
}

Ok(())
``` [1](#0-0) 

When the caller is an **already-registered participant** (`voter_account().is_ok()`) **and** the attestation is an **update** rather than a first insertion (`attestation_insertion_result != NewlyInsertedParticipant`), both sub-conditions are `false`, so `attestation_storage_must_be_paid_by_caller` is `false`. The entire `if` block — including the deposit read, the minimum-deposit enforcement, and the excess refund — is bypassed. The function returns `Ok(())` and the attached NEAR is permanently retained by the contract. [2](#0-1) 

The function is declared `#[payable]`, so the NEAR runtime accepts any deposit amount without restriction. [3](#0-2) 

---

### Impact Explanation

Any NEAR attached by an existing participant during a re-attestation call is permanently locked inside the MPC contract. There is no withdrawal path. The contract's balance silently grows by the attached amount, and the participant's account is debited with no corresponding state benefit. This breaks the production accounting invariant that excess deposits must always be refunded — the same invariant enforced correctly for new participants and non-participants in the same function.

**Impact class:** Medium — balance/accounting invariant break that causes direct, permanent loss of caller funds without requiring network-level DoS or operator misconfiguration.

---

### Likelihood Explanation

MPC nodes re-submit their attestation on a **one-hour cadence** and on every attestation-removal event, as documented in `crates/node/src/tee/remote_attestation.rs`. [4](#0-3) 

The operator documentation explicitly states that first-time joiners must attach a deposit and instructs operators to call `submit_participant_info` manually with `--deposit` when the node fails to do so automatically. A node or operator that attaches a deposit on a re-submission (e.g., after a TEE upgrade, after an attestation-removal event, or due to a misconfigured deposit amount) will silently lose those funds. The test harness itself attaches `NearToken::from_near(1)` on every `submit_participant_info` call regardless of whether it is a first or subsequent submission, confirming the pattern is realistic. [5](#0-4) 

---

### Recommendation

Remove the conditional guard around the refund logic. The excess-deposit refund must execute unconditionally whenever `env::attached_deposit()` exceeds zero, regardless of whether the submission is new or an update and regardless of whether the caller is already a participant:

```rust
// After add_participant succeeds:
let attached = env::attached_deposit();

if attestation_storage_must_be_paid_by_caller {
    let storage_used = env::storage_usage().saturating_sub(initial_storage);
    let cost = env::storage_byte_cost().saturating_mul(storage_used as u128);
    if attached < cost {
        return Err(...);
    }
    // refund excess below
}

// Always refund any deposit not consumed by storage cost
let cost_charged = if attestation_storage_must_be_paid_by_caller {
    env::storage_byte_cost().saturating_mul(
        env::storage_usage().saturating_sub(initial_storage) as u128
    )
} else {
    NearToken::from_yoctonear(0)
};
if let Some(diff) = attached.checked_sub(cost_charged)
    && diff > NearToken::from_yoctonear(0)
{
    Promise::new(account_id).transfer(diff).detach();
}
```

Alternatively, add a `#[deposit(0)]` guard or explicitly reject any non-zero deposit when `!attestation_storage_must_be_paid_by_caller`, so callers receive an immediate error rather than a silent fund loss.

---

### Proof of Concept

1. Deploy the MPC contract and initialize it with at least one participant (account `alice.near`).
2. Submit a valid attestation for `alice.near` so she is registered (`NewlyInsertedParticipant`). Confirm `voter_account()` returns `Ok` for her.
3. Call `submit_participant_info` again from `alice.near` with `attached_deposit = NearToken::from_near(1)` (a re-submission / update).
   - `is_new_attestation` = `false` (entry already exists, result is `UpdatedParticipant` or equivalent).
   - `caller_is_not_participant` = `false` (she is a registered voter).
   - `attestation_storage_must_be_paid_by_caller` = `false`.
   - The `if` block is skipped entirely.
   - Function returns `Ok(())`.
4. Check `alice.near`'s balance: it has decreased by 1 NEAR (plus gas).
5. Check the contract's balance: it has increased by 1 NEAR.
6. No refund receipt is ever scheduled. The 1 NEAR is permanently locked. [1](#0-0)

### Citations

**File:** crates/contract/src/lib.rs (L758-760)
```rust
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

**File:** crates/contract/src/lib.rs (L3993-4000)
```rust
        let participant_context = VMContextBuilder::new()
            .signer_account_id(account_id.clone())
            .predecessor_account_id(account_id.clone())
            .attached_deposit(NearToken::from_near(1))
            .build();
        testing_env!(participant_context);

        contract.submit_participant_info(Attestation::Mock(attestation), dto_public_key)
```

**File:** crates/node/src/tee/remote_attestation.rs (L37-68)
```rust
pub async fn submit_remote_attestation(
    tx_sender: impl TransactionSender,
    attestation: Attestation,
    tls_public_key: Ed25519PublicKey,
) -> anyhow::Result<()> {
    let submit_participant_info_args = contract_args::SubmitParticipantInfoArgs::new(
        attestation.into_contract_interface_type(),
        tls_public_key,
    );

    let set_attestation = move || {
        let tx_sender = tx_sender.clone();
        let propose_join_args_clone = submit_participant_info_args.clone();
        let chain_args =
            ChainSendTransactionRequest::SubmitParticipantInfo(Box::new(propose_join_args_clone));

        async move {
            let attestation_submission_response = tx_sender
                .send_and_wait(chain_args)
                .await
                .context("failed to submit transaction")?;

            match attestation_submission_response {
                TransactionStatus::Executed => Ok(()),
                TransactionStatus::NotExecuted => {
                    anyhow::bail!("attestation submission was not executed")
                }
                TransactionStatus::Unknown => {
                    anyhow::bail!("attestation submission has unknown response")
                }
            }
        }
```
