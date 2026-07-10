### Title
Attached NEAR Deposit Silently Trapped When Existing Participant Re-submits Attestation - (File: `crates/contract/src/lib.rs`)

---

### Summary

`submit_participant_info` is marked `#[payable]` but contains a conditional deposit-handling block that is entirely skipped when an existing protocol participant updates their own attestation entry. Any NEAR tokens attached to such a call are accepted by the contract and permanently locked, with no refund issued and no revert triggered.

---

### Finding Description

`submit_participant_info` computes a boolean gate before deciding whether to handle the attached deposit:

```rust
let caller_is_not_participant = self.voter_account().is_err();
let is_new_attestation = matches!(
    attestation_insertion_result,
    ParticipantInsertion::NewlyInsertedParticipant
);

let attestation_storage_must_be_paid_by_caller =
    is_new_attestation || caller_is_not_participant;

if attestation_storage_must_be_paid_by_caller {
    // ... deposit check and refund logic ...
}

Ok(())
``` [1](#0-0) 

When both conditions are `false` — i.e., the caller **is** already a protocol participant (`voter_account()` succeeds) **and** the attestation is an **update** to an existing entry (`add_participant` returns `UpdatedExistingParticipant`) — the entire `if` block is bypassed. The function returns `Ok(())` without ever reading `env::attached_deposit()` or issuing a refund promise.

`add_participant` explicitly supports and returns `UpdatedExistingParticipant` for re-submissions by the same account:

```rust
Ok(match insertion {
    Some(_previous_attestation) => ParticipantInsertion::UpdatedExistingParticipant,
    None => ParticipantInsertion::NewlyInsertedParticipant,
})
``` [2](#0-1) 

This is a documented, tested, and expected code path — participants are expected to re-submit attestations during TEE upgrades or key rotations: [3](#0-2) 

The contract exposes no owner-withdrawal or sweep mechanism for accidentally deposited funds, so any trapped NEAR is permanently locked.

---

### Impact Explanation

Any NEAR tokens attached to a re-submission call by an existing participant are silently consumed by the contract. The transaction succeeds, the attestation is updated, and the caller receives no error and no refund. Because TEE upgrades and key rotations are routine operational events that require re-submission, this is a realistic loss path for every active MPC node operator. The contract has no recovery mechanism for trapped funds.

This matches the allowed Medium impact: **"Balance, request-lifecycle, participant-state, or contract execution-flow manipulation that breaks production safety/accounting invariants."**

---

### Likelihood Explanation

Every existing MPC participant must call `submit_participant_info` again whenever they rotate their TEE attestation (e.g., after a software upgrade). The NEAR SDK's `#[payable]` attribute does not enforce a zero-deposit requirement, so a caller who mistakenly attaches any non-zero deposit — or whose tooling defaults to attaching a small deposit — will silently lose those funds. The triggering condition (`UpdatedExistingParticipant` + caller is a participant) is the normal steady-state re-submission path, not an edge case.

---

### Recommendation

When `attestation_storage_must_be_paid_by_caller` is `false`, the function should still check whether any deposit was attached and refund it in full. Alternatively, the function can assert that `env::attached_deposit() == 0` for the update path and panic with a clear error if a non-zero deposit is provided:

```rust
if attestation_storage_must_be_paid_by_caller {
    // existing deposit check + refund logic
} else {
    // Refund any accidentally attached deposit for free re-submissions
    let attached = env::attached_deposit();
    if attached > NearToken::from_yoctonear(0) {
        Promise::new(account_id).transfer(attached).detach();
    }
}
```

---

### Proof of Concept

1. Contract is in `Running` state with participant `alice.near` already registered and attested.
2. `alice.near` calls `submit_participant_info` with a fresh valid attestation (e.g., after a TEE upgrade) and accidentally attaches `1 NEAR` as deposit.
3. Inside the function:
   - `add_participant` succeeds and returns `ParticipantInsertion::UpdatedExistingParticipant` → `is_new_attestation = false`. [4](#0-3) 
   - `voter_account()` succeeds because `alice.near` is in the active participant set → `caller_is_not_participant = false`. [5](#0-4) 
   - `attestation_storage_must_be_paid_by_caller = false || false = false`. [6](#0-5) 
   - The deposit-handling `if` block is skipped entirely. [7](#0-6) 
4. The function returns `Ok(())`. The `1 NEAR` is now held by the contract with no refund promise issued and no withdrawal path available.

### Citations

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

**File:** crates/contract/src/tee/tee_state.rs (L191-202)
```rust
        let insertion = self.stored_attestations.insert(
            tls_pk,
            NodeAttestation {
                node_id,
                verified_attestation,
            },
        );

        Ok(match insertion {
            Some(_previous_attestation) => ParticipantInsertion::UpdatedExistingParticipant,
            None => ParticipantInsertion::NewlyInsertedParticipant,
        })
```

**File:** crates/contract/src/tee/tee_state.rs (L1420-1454)
```rust
    #[test]
    fn add_participant__should_allow_same_account_to_update_its_own_entry() {
        // Given: an existing attestation registered to `alice.near`.
        const TEE_UPGRADE_DURATION: Duration = Duration::from_secs(10_000);

        let mut tee_state = TeeState::default();
        let tls_public_key = bogus_ed25519_public_key();

        let initial_node = NodeId {
            account_id: "alice.near".parse().unwrap(),
            tls_public_key: tls_public_key.clone(),
            account_public_key: bogus_ed25519_public_key(),
        };
        tee_state
            .add_participant(
                initial_node,
                Attestation::Mock(MockAttestation::Valid),
                TEE_UPGRADE_DURATION,
            )
            .expect("initial insertion should succeed");

        // When: the same account resubmits with a rotated account_public_key.
        let rotated_node = NodeId {
            account_id: "alice.near".parse().unwrap(),
            tls_public_key,
            account_public_key: bogus_ed25519_public_key(),
        };
        let result = tee_state.add_participant(
            rotated_node.clone(),
            Attestation::Mock(MockAttestation::Valid),
            TEE_UPGRADE_DURATION,
        );

        // Then: the update is accepted and the stored entry reflects the new key.
        assert_matches!(result, Ok(ParticipantInsertion::UpdatedExistingParticipant));
```
