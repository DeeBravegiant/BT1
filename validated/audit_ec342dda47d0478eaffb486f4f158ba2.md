### Title
Attached Deposit Not Refunded in `submit_participant_info` When Storage Cost Is Not Charged to Caller - (`File: crates/contract/src/lib.rs`)

### Summary

`submit_participant_info` is a `#[payable]` function that conditionally enters a deposit-handling block. When the condition `attestation_storage_must_be_paid_by_caller` evaluates to `false`, the entire deposit block — including the refund path — is skipped. Any NEAR tokens attached to the call in that branch are permanently frozen on the contract balance.

### Finding Description

In `submit_participant_info`, after inserting the attestation, the contract computes:

```rust
let attestation_storage_must_be_paid_by_caller =
    is_new_attestation || caller_is_not_participant;

if attestation_storage_must_be_paid_by_caller {
    // ... check deposit >= cost, refund excess
}
// No else branch — attached deposit silently consumed when condition is false
``` [1](#0-0) 

The condition is `false` when both of the following hold simultaneously:
- `is_new_attestation == false` → the caller is updating an **existing** attestation entry (`ParticipantInsertion::UpdatedExistingParticipant`)
- `caller_is_not_participant == false` → the caller **is** a current voter/participant [2](#0-1) 

In this branch, `env::attached_deposit()` is never read, never consumed for storage, and never refunded. The deposit is silently absorbed into the contract's balance with no accounting entry and no recovery path.

Compare this to the `propose_update` function, which correctly refunds any excess deposit unconditionally: [3](#0-2) 

And `require_deposit`, which always refunds excess for `sign`/`request_app_private_key`: [4](#0-3) 

`submit_participant_info` has no equivalent unconditional refund.

### Impact Explanation

Any NEAR tokens attached to a `submit_participant_info` call by an existing participant performing an attestation renewal are permanently frozen on the MPC contract balance. There is no admin withdrawal function, no sweep mechanism, and no recovery path. This breaks the production accounting invariant that a `#[payable]` function must either consume or return every yoctoNEAR attached to it.

This maps to: **Medium — balance/accounting invariant break that freezes caller funds on the contract without relying on network-level DoS or operator misconfiguration.**

### Likelihood Explanation

The `mpc-node`'s `periodic_attestation_submission` task attaches `0` for re-submissions in practice (documented explicitly: "the node attaches `0`"). However:

1. The function is publicly callable by any NEAR account — any existing participant who manually calls `submit_participant_info` with a deposit (e.g., following the documented first-time flow that requires a deposit) during a re-submission will lose those funds.
2. A future code change to the node that attaches a deposit for re-submissions would silently freeze funds at scale across all periodic re-attestations.
3. The function is `#[payable]` with no guard rejecting non-zero deposits in the `false` branch, so the contract actively accepts and freezes the funds without warning.

### Recommendation

Add an unconditional refund of any attached deposit when `attestation_storage_must_be_paid_by_caller` is `false`:

```rust
if attestation_storage_must_be_paid_by_caller {
    // existing storage cost check and refund
} else {
    let attached = env::attached_deposit();
    if attached > NearToken::from_yoctonear(0) {
        Promise::new(account_id).transfer(attached).detach();
    }
}
```

Alternatively, reject non-zero deposits in the `else` branch with a clear error, consistent with the contract's defensive style.

### Proof of Concept

1. Alice is an existing MPC participant (her account passes `self.voter_account().is_ok()`).
2. Alice's attestation is already stored (`stored_attestations` contains her TLS key).
3. Alice calls `submit_participant_info` with `attached_deposit = 1_000_000 yoctoNEAR` (e.g., following the documented first-time deposit guidance by mistake, or via a manual NEAR CLI call).
4. `add_participant` succeeds and returns `ParticipantInsertion::UpdatedExistingParticipant` → `is_new_attestation = false`.
5. `self.voter_account().is_ok()` → `caller_is_not_participant = false`.
6. `attestation_storage_must_be_paid_by_caller = false || false = false`.
7. The `if` block is skipped entirely. `env::attached_deposit()` is never called. The 1,000,000 yoctoNEAR is absorbed into the contract balance.
8. Alice's balance is permanently reduced with no receipt, no log, and no recovery. [5](#0-4)

### Citations

**File:** crates/contract/src/lib.rs (L134-139)
```rust
        Some(diff) => {
            if diff > NearToken::from_yoctonear(0) {
                log!("refund excess deposit {diff} to {predecessor}");
                Promise::new(predecessor.clone()).transfer(diff).detach();
            }
        }
```

**File:** crates/contract/src/lib.rs (L758-852)
```rust
    #[payable]
    #[handle_result]
    pub fn submit_participant_info(
        &mut self,
        proposed_participant_attestation: dtos::Attestation,
        tls_public_key: dtos::Ed25519PublicKey,
    ) -> Result<(), Error> {
        let proposed_participant_attestation =
            proposed_participant_attestation.try_into_contract_type()?;

        let account_key = env::signer_account_pk();
        let account_id = Self::assert_caller_is_signer();

        log!(
            "submit_participant_info: signer={}, proposed_participant_attestation={:?}, account_key={:?}",
            account_id,
            proposed_participant_attestation,
            account_key
        );

        // Save the initial storage usage to know how much to charge the proposer for the storage
        // used
        let initial_storage = env::storage_usage();

        let tee_upgrade_deadline_duration =
            Duration::from_secs(self.config.tee_upgrade_deadline_duration_seconds);

        // The node always signs submissions with an Ed25519 key
        // (`near_signer_key`), so the signer key here is Ed25519 in practice.
        // Reject non-Ed25519 signer keys rather than silently storing a value
        // we could never match against in `is_caller_an_attested_participant`.
        let account_public_key = dtos::Ed25519PublicKey::try_from(&account_key).map_err(|_| {
            InvalidParameters::InvalidTeeRemoteAttestation {
                reason: "signer account key must be Ed25519".to_string(),
            }
        })?;

        // Add the participant information to the contract state
        let attestation_insertion_result = self
            .tee_state
            .add_participant(
                NodeId {
                    account_id: account_id.clone(),
                    tls_public_key,
                    account_public_key,
                },
                proposed_participant_attestation,
                tee_upgrade_deadline_duration,
            )
            .map_err(|err| {
                let reason = match &err {
                    AttestationSubmissionError::InvalidAttestation(_) => {
                        format!("TeeQuoteStatus is invalid: {err}")
                    }
                    AttestationSubmissionError::TlsKeyOwnedByOtherAccount => err.to_string(),
                };
                InvalidParameters::InvalidTeeRemoteAttestation { reason }
            })?;

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

**File:** crates/contract/src/lib.rs (L1326-1331)
```rust
        // Refund the difference if the proposer attached more than required.
        if let Some(diff) = attached.checked_sub(required)
            && diff > NearToken::from_yoctonear(0)
        {
            Promise::new(proposer).transfer(diff).detach();
        }
```
