### Title
Unrefunded Deposit in `submit_participant_info` When Caller Is an Existing Participant — (File: `crates/contract/src/lib.rs`)

---

### Summary

The `submit_participant_info` function is marked `#[payable]` and therefore accepts attached NEAR deposits, but contains a conditional code path that silently discards any attached deposit without refunding it. When an existing protocol participant re-submits their TEE attestation, the deposit check and refund logic is entirely skipped, permanently locking any attached NEAR tokens inside the contract.

---

### Finding Description

The function `submit_participant_info` computes a boolean flag to decide whether to charge the caller for storage: [1](#0-0) 

```rust
let caller_is_not_participant = self.voter_account().is_err();
let is_new_attestation = matches!(
    attestation_insertion_result,
    ParticipantInsertion::NewlyInsertedParticipant
);
let attestation_storage_must_be_paid_by_caller =
    is_new_attestation || caller_is_not_participant;
```

When `attestation_storage_must_be_paid_by_caller` is `true`, the function correctly reads `env::attached_deposit()`, validates it against the storage cost, and refunds any excess: [2](#0-1) 

However, when the flag is `false` — i.e., the caller is already a protocol participant **and** the attestation is not brand-new — the entire deposit-handling block is skipped. The function is still `#[payable]`, so the NEAR runtime accepts any attached deposit without complaint, but the contract never reads it and never issues a refund. The tokens are permanently locked in the contract. [3](#0-2) 

This is the direct NEAR analog of the reported Solidity pattern: a function that is supposed to handle value correctly but fails to do so in a specific execution path, causing permanent fund loss instead of a revert.

---

### Impact Explanation

**Medium.** The accounting invariant that "excess deposits must be refunded" — upheld by every other deposit-accepting function in the contract (`sign`, `request_app_private_key`, `propose_update`) — is violated here. Any NEAR tokens attached to a re-submission call by an existing participant are permanently frozen inside the chain-signature contract. Because the contract has no general withdrawal mechanism, recovery is impossible without a contract upgrade. This breaks the production safety/accounting invariant for participant-state management calls. [4](#0-3) 

---

### Likelihood Explanation

**Low-to-Medium.** Participants re-submit their attestation during TEE software upgrades (a routine operational event). A participant who follows the same pattern as `sign` or `request_app_private_key` — attaching 1 yoctoNEAR or more "just in case" — will silently lose those tokens. The function's own doc comment notes it "might require a deposit," increasing the chance of accidental attachment. [5](#0-4) 

---

### Recommendation

Add an unconditional refund of any attached deposit when `attestation_storage_must_be_paid_by_caller` is `false`:

```rust
} else {
    let attached = env::attached_deposit();
    if attached > NearToken::from_yoctonear(0) {
        Promise::new(account_id).transfer(attached).detach();
    }
}
```

This mirrors the refund pattern already used in `sign`, `request_app_private_key`, and `propose_update`.

---

### Proof of Concept

1. Participant `alice.near` is an active protocol participant with an existing TEE attestation stored in `tee_state`.
2. Alice's node software is upgraded; she calls `submit_participant_info` with `attached_deposit = 1_000_000 yoctoNEAR` (following the pattern of other payable endpoints).
3. Inside the function, `caller_is_not_participant` is `false` (Alice is a participant) and `is_new_attestation` is `false` (her entry already exists), so `attestation_storage_must_be_paid_by_caller = false`.
4. The `if attestation_storage_must_be_paid_by_caller { … }` block is skipped entirely — `env::attached_deposit()` is never read and no `Promise::transfer` is issued.
5. The call succeeds. Alice's `1_000_000 yoctoNEAR` is permanently locked in the MPC contract with no recovery path. [6](#0-5)

### Citations

**File:** crates/contract/src/lib.rs (L100-141)
```rust
/// Minimum deposit required for sign requests
const MINIMUM_SIGN_REQUEST_DEPOSIT: NearToken = NearToken::from_yoctonear(1);

/// Minimum deposit required for CKD requests
const MINIMUM_CKD_REQUEST_DEPOSIT: NearToken = NearToken::from_yoctonear(1);

/// Entries to scan in the post-reshare `clean_invalid_attestations` sweep. External
/// callers may pick a different value; this only governs the automatic invocation.
const RESHARE_CLEAN_INVALID_ATTESTATIONS_MAX_SCAN: u32 = 100;

/// Checks that the caller attached at least `minimum_deposit` and refunds any excess.
///
/// A non-zero deposit is required so that the transaction must be signed by a
/// full-access key (or a function-call access key whose `deposit` allowance is
/// explicitly set). This prevents a **malicious frontend** from silently
/// submitting signature requests on behalf of a user via a restricted
/// function-call access key, because such keys cannot attach deposits by
/// default. In other words, requiring a deposit ensures the user (or their
/// full-access key) explicitly authorised the call.
///
/// See the "Deposit requirement" section in the contract README for more
/// details.
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
