### Title
State Written Before Deposit Validation in `submit_participant_info` Allows Free Storage Occupation - (File: `crates/contract/src/lib.rs`)

---

### Summary

`submit_participant_info` mutates `self.tee_state.stored_attestations` via `add_participant` **before** it validates the caller's attached deposit. In NEAR, `#[handle_result]` functions that return `Err` do **not** roll back state — only `panic!` / `env::panic_str` does. Therefore, when the deposit check at line 835 fires `return Err(InsufficientDeposit)`, the attestation entry written at line 191 of `tee_state.rs` is already committed to on-chain storage. Any caller with a valid (including mock) attestation can permanently store their participant record at zero cost, forcing the contract to absorb the storage-staking charge.

---

### Finding Description

The execution order in `submit_participant_info` is:

1. **Line 780** — snapshot `initial_storage = env::storage_usage()`
2. **Lines 796–815** — call `self.tee_state.add_participant(...)` with `?`; on success this executes `self.stored_attestations.insert(tls_pk, NodeAttestation { … })` (`tee_state.rs:191`), permanently writing to contract storage
3. **Lines 826–841** — only now compute `storage_used`, derive `cost`, read `attached_deposit`, and gate on `attached < cost`

If the deposit is insufficient the function executes:

```rust
return Err(InvalidParameters::InsufficientDeposit { … }.into());
```

Because `#[handle_result]` serialises the `Err` variant and returns normally (no panic), the NEAR runtime commits the storage delta from step 2. The `stored_attestations` entry survives. The contract's own NEAR balance is debited for the storage bytes; the caller pays nothing.

This is structurally identical to the ConvexMasterChef M-13 pattern: a quantity that should gate a state write (`lpSupply` / `attached_deposit`) is read **after** the write it is supposed to guard, so the guard is ineffective and the accounting invariant is broken.

---

### Impact Explanation

Any account that can produce a passing attestation (including `Attestation::Mock(MockAttestation::Valid)`, which `add_participant` accepts unconditionally per `tee_state.rs:169`) can:

- Call `submit_participant_info` with `attached_deposit = 0`
- Have their `NodeAttestation` entry committed to `stored_attestations` for free
- Repeat with different TLS keys to accumulate unbounded free storage at the contract's expense

The contract's NEAR balance is reduced by `storage_byte_cost × bytes_written` per exploit call, with no corresponding deposit from the caller. This breaks the production storage-accounting invariant and constitutes a direct, measurable financial drain on the MPC contract's balance.

---

### Likelihood Explanation

The entry path is a public, payable contract method callable by any NEAR account. No threshold collusion, TEE hardware, or privileged role is required when mock attestations are enabled (which they are in the current codebase). Even with only real Dstack attestations accepted, every legitimate node operator can trigger the bug by simply omitting or under-paying the deposit. The exploit requires no special knowledge beyond reading the contract ABI.

---

### Recommendation

Move the deposit validation **before** the `add_participant` call, or replace `return Err(…)` with `env::panic_str(…)` so that NEAR's panic-rollback mechanism undoes the storage write on failure. The safest fix is the former:

```rust
// 1. Compute expected cost from a dry-run or a pre-computed upper bound,
//    OR validate deposit first and panic (not Err) on shortfall.
let initial_storage = env::storage_usage();
// ... validate deposit here, panic if insufficient ...
let attestation_insertion_result = self.tee_state.add_participant(...)?;
```

Alternatively, use `env::panic_str` instead of `return Err` at line 836 so the runtime rolls back the storage write atomically.

---

### Proof of Concept

```
1. Attacker calls submit_participant_info(
       Attestation::Mock(MockAttestation::Valid),
       attacker_tls_key,
       attached_deposit = 0 yoctoNEAR
   )

2. add_participant() succeeds → stored_attestations.insert(attacker_tls_key, …)
   [contract storage grows; NEAR balance debited]

3. Deposit check: 0 < cost → return Err(InsufficientDeposit)
   [#[handle_result] serialises Err, returns normally — NO rollback]

4. On-chain state: attacker's NodeAttestation is permanently stored.
   Contract balance: reduced by storage_byte_cost × entry_size.
   Attacker paid: 0.
```

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/contract/src/lib.rs (L780-815)
```rust
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
```

**File:** crates/contract/src/lib.rs (L826-841)
```rust
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
```

**File:** crates/contract/src/tee/tee_state.rs (L151-202)
```rust
    pub(crate) fn add_participant(
        &mut self,
        node_id: NodeId,
        attestation: Attestation,
        tee_upgrade_deadline_duration: Duration,
    ) -> Result<ParticipantInsertion, AttestationSubmissionError> {
        let expected_report_data: ReportData = ReportDataV1::new(
            *node_id.tls_public_key.as_bytes(),
            *node_id.account_public_key.as_bytes(),
        )
        .into();

        let accepted_measurements = self.get_accepted_measurements();
        // TODO(#3264): run DCAP in the verifier contract (Promise + callback) and
        // do the post-DCAP checks here, instead of verifying locally in-WASM.
        let AcceptedAttestation {
            attestation: verified_attestation,
            advisory_ids,
        } = attestation.verify_locally(
            expected_report_data.into(),
            Self::current_time_seconds(),
            &self.get_allowed_mpc_docker_image_hashes(tee_upgrade_deadline_duration),
            &self.get_allowed_launcher_compose_hashes(),
            &accepted_measurements,
        )?;

        log_informational_advisory_ids(&advisory_ids);

        let tls_pk = node_id.tls_public_key.clone();

        // Authorization: a TLS key registered to one account must not be
        // overwritten by a submission from a different account. Without this,
        // any caller could replace any participant's stored attestation, since
        // the entry is keyed only by `tls_public_key`.
        if let Some(existing) = self.stored_attestations.get(&tls_pk)
            && existing.node_id.account_id != node_id.account_id
        {
            return Err(AttestationSubmissionError::TlsKeyOwnedByOtherAccount);
        }

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
