### Title
Excess Deposit Not Refunded on Attestation Re-Submission Branch in `submit_participant_info` - (File: `crates/contract/src/lib.rs`)

---

### Summary

`submit_participant_info` is a `#[payable]` function that conditionally enforces a deposit only when storage must be paid by the caller. When an existing participant re-submits their attestation (the update path), the deposit-handling block is skipped entirely. Any NEAR tokens attached to that call are silently consumed by the contract with no refund.

---

### Finding Description

`submit_participant_info` computes a boolean gate to decide whether to enforce and refund a deposit:

```rust
let attestation_storage_must_be_paid_by_caller =
    is_new_attestation || caller_is_not_participant;

if attestation_storage_must_be_paid_by_caller {
    // ... check attached >= cost, refund excess
}
``` [1](#0-0) 

The condition is `false` — and the block is skipped — when **both**:
- `is_new_attestation == false` (the TLS key already exists in `stored_attestations`, so `add_participant` returns `UpdatedExistingParticipant`)
- `caller_is_not_participant == false` (the caller is already a voting participant) [2](#0-1) 

In that branch the function returns `Ok(())` without ever reading `env::attached_deposit()`. Because the function is declared `#[payable]`, NEAR accepts any deposit the caller attaches; the runtime does not auto-refund it. The tokens are permanently credited to the contract account. [3](#0-2) 

The analogous refund logic that *does* exist for the new-attestation path (and for `propose_update` and `require_deposit`) demonstrates the intended pattern — refund the difference when more than required is attached — but that pattern is absent for the update path. [4](#0-3) 

---

### Impact Explanation

Any NEAR tokens attached to a re-submission call by an existing participant are permanently locked in the contract. There is no recovery path: the contract has no withdrawal function for accidentally deposited funds, and the tokens are not credited to any participant balance. This breaks the accounting invariant that a `#[payable]` function must either consume exactly the required amount or refund the remainder.

---

### Likelihood Explanation

The MPC node daemon normally attaches zero deposit to periodic re-submissions (the documentation explicitly notes this and instructs operators to call manually with `--deposit` only for first-time joins). [5](#0-4) 

However, a human operator who manually re-submits an attestation (e.g., to refresh an expiring quote) may attach a deposit "just to be safe" — exactly the over-payment pattern described in the reference report. The likelihood is medium: the automated path is safe, but the manual path is a realistic operator mistake with no on-chain warning.

---

### Recommendation

Add an unconditional refund of any attached deposit when `attestation_storage_must_be_paid_by_caller` is `false`:

```rust
} else {
    // No storage cost for this re-submission; refund any deposit the caller attached.
    let attached = env::attached_deposit();
    if attached > NearToken::from_yoctonear(0) {
        Promise::new(account_id).transfer(attached).detach();
    }
}
```

Alternatively, reject non-zero deposits on the update path with an explicit error, mirroring the pattern used in `require_deposit`.

---

### Proof of Concept

1. Participant `alice.near` has already submitted a valid attestation (entry exists in `stored_attestations`; `alice.near` is in the active participant set).
2. Alice's attestation is approaching expiry. She manually calls `submit_participant_info` with a fresh quote and attaches `1 NEAR` as a precaution.
3. `add_participant` returns `UpdatedExistingParticipant` → `is_new_attestation = false`.
4. `voter_account()` succeeds for Alice → `caller_is_not_participant = false`.
5. `attestation_storage_must_be_paid_by_caller = false || false = false`.
6. The `if` block is skipped; `env::attached_deposit()` is never read.
7. The call returns `Ok(())`; Alice's `1 NEAR` is permanently retained by the contract. [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L134-138)
```rust
        Some(diff) => {
            if diff > NearToken::from_yoctonear(0) {
                log!("refund excess deposit {diff} to {predecessor}");
                Promise::new(predecessor.clone()).transfer(diff).detach();
            }
```

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

**File:** crates/contract/src/tee/tee_state.rs (L199-202)
```rust
        Ok(match insertion {
            Some(_previous_attestation) => ParticipantInsertion::UpdatedExistingParticipant,
            None => ParticipantInsertion::NewlyInsertedParticipant,
        })
```
