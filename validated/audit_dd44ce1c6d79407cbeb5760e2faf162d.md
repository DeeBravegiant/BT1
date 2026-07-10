### Title
Excess Deposit Silently Absorbed in `submit_participant_info` for Existing-Participant Re-submissions — (File: `crates/contract/src/lib.rs`)

---

### Summary

`submit_participant_info` is marked `#[payable]` and accepts NEAR deposits. However, when an **existing participant** re-submits their attestation (producing `ParticipantInsertion::UpdatedExistingParticipant`), the entire deposit-check-and-refund block is skipped. Any NEAR tokens attached to that call are permanently absorbed by the contract with no refund issued.

---

### Finding Description

The deposit-handling logic in `submit_participant_info` is gated on a single boolean:

```rust
let attestation_storage_must_be_paid_by_caller =
    is_new_attestation || caller_is_not_participant;

if attestation_storage_must_be_paid_by_caller {
    // ... check attached >= cost, refund excess
}
``` [1](#0-0) 

`is_new_attestation` is `true` only when `add_participant` returns `ParticipantInsertion::NewlyInsertedParticipant`. [2](#0-1) 

`caller_is_not_participant` is `true` only when `voter_account()` returns an error (i.e., the caller is not in the current participant set). [3](#0-2) 

When an **existing participant** re-submits (e.g., for a TEE attestation refresh), `add_participant` returns `UpdatedExistingParticipant`, so `is_new_attestation = false`. The caller is already a participant, so `caller_is_not_participant = false`. Both conditions are false, `attestation_storage_must_be_paid_by_caller = false`, and the `if` block is never entered. Any NEAR attached to the call is silently kept by the contract.

The function is declared `#[payable]`, so the NEAR runtime does not reject a non-zero deposit; it simply credits the contract account. [4](#0-3) 

The `tee_state.add_participant` path that produces `UpdatedExistingParticipant` is confirmed reachable in the test suite: [5](#0-4) 

---

### Impact Explanation

Any NEAR tokens attached to a re-submission call by an existing participant are permanently locked inside the contract. The contract's balance grows by the attached amount; the participant's balance shrinks by the same amount. There is no recovery path: the contract has no withdrawal mechanism for accidentally absorbed deposits. This breaks the accounting invariant that every payable function must refund any deposit it does not consume.

Impact: **Medium** — balance/accounting invariant violation; funds permanently frozen inside the chain-signature contract.

---

### Likelihood Explanation

The automated node software always attaches `0` deposit for re-submissions (documented explicitly: *"the node attaches `0`, so call `submit_participant_info` manually with `--deposit` once"*). [6](#0-5) 

However, the same documentation instructs operators to call `submit_participant_info` **manually with `--deposit`** for first-time joins. An operator who is already a participant (e.g., after a key rotation or TEE upgrade) and follows this advice for a re-submission will silently lose the attached deposit. The entry path is a direct, unprivileged NEAR transaction — no special access is required beyond holding a participant account.

---

### Recommendation

Add an unconditional refund for any non-zero deposit when `attestation_storage_must_be_paid_by_caller` is `false`:

```rust
} else {
    // Existing participant re-submission: no storage cost, refund any deposit.
    let attached = env::attached_deposit();
    if attached > NearToken::from_yoctonear(0) {
        Promise::new(account_id).transfer(attached).detach();
    }
}
```

Alternatively, reject a non-zero deposit in this branch with an explicit error, mirroring the pattern used in `require_deposit`. [7](#0-6) 

---

### Proof of Concept

1. Alice is an existing MPC participant (`voter_account()` returns `Ok`).
2. Alice's TEE attestation is due for renewal; she calls `submit_participant_info` manually, attaching `1 NEAR` following operator documentation.
3. `add_participant` succeeds and returns `ParticipantInsertion::UpdatedExistingParticipant`.
4. `is_new_attestation = false`, `caller_is_not_participant = false` → `attestation_storage_must_be_paid_by_caller = false`.
5. The `if` block is skipped entirely; no deposit check, no refund.
6. The function returns `Ok(())`.
7. Alice's `1 NEAR` is permanently credited to the contract account with no recovery path. [8](#0-7)

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

**File:** crates/contract/src/lib.rs (L758-760)
```rust
    #[payable]
    #[handle_result]
    pub fn submit_participant_info(
```

**File:** crates/contract/src/lib.rs (L817-817)
```rust
        let caller_is_not_participant = self.voter_account().is_err();
```

**File:** crates/contract/src/lib.rs (L818-821)
```rust
        let is_new_attestation = matches!(
            attestation_insertion_result,
            ParticipantInsertion::NewlyInsertedParticipant
        );
```

**File:** crates/contract/src/lib.rs (L823-851)
```rust
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

**File:** crates/contract/src/tee/tee_state.rs (L800-810)
```rust
        let re_insertion_result = tee_state.add_participant(
            participant_id.clone(),
            local_attestation.clone(),
            TEE_UPGRADE_DURATION,
        );

        // then
        assert_matches!(
            re_insertion_result,
            Ok(ParticipantInsertion::UpdatedExistingParticipant)
        );
```

**File:** docs/running-an-mpc-node-in-tdx-external-guide.md (L2039-2039)
```markdown
- **`Attached deposit is lower than required. Attached: X, required: Y`** — first-time joiners must attach enough yoctoNEAR for storage; the node attaches `0`, so call `submit_participant_info` manually with `--deposit` once. Exact amount tracked in [#903](https://github.com/near/mpc/issues/903).
```
