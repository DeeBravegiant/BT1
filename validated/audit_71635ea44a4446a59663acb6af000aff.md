### Title
Stale Attestation Check in `assert_caller_is_attested_participant_and_protocol_active` Allows Nodes with Expired or Revoked Attestations to Submit Signatures and Cast Protocol Votes - (File: `crates/contract/src/lib.rs`, `crates/contract/src/tee/tee_state.rs`)

---

### Summary

`assert_caller_is_attested_participant_and_protocol_active` calls `is_caller_an_attested_participant`, which only checks whether a stored attestation entry *exists* for the caller — it does not re-verify whether that attestation is still valid (i.e., not expired and still matching an approved image hash). The re-verification function `reverify_participants` is only invoked inside `verify_tee()`, which is called at most every two days. During the window between `verify_tee()` calls, a node whose attestation has expired or whose image hash has been governance-revoked can still successfully call `respond`, `vote_pk`, `vote_reshared`, `start_keygen_instance`, `start_reshare_instance`, and `vote_abort_key_event_instance` — all of which are gated solely on the stale existence check.

---

### Finding Description

`assert_caller_is_attested_participant_and_protocol_active` is the single attestation guard for all node-facing protocol methods:

```rust
// crates/contract/src/lib.rs:2389-2403
fn assert_caller_is_attested_participant_and_protocol_active(&self) {
    let participants = self.protocol_state.active_participants();
    Self::assert_caller_is_signer();
    let attestation_check = self
        .tee_state
        .is_caller_an_attested_participant(participants);
    assert_matches::assert_matches!(
        attestation_check,
        Ok(()),
        "Caller must be an attested participant"
    );
}
``` [1](#0-0) 

`is_caller_an_attested_participant` only verifies that an entry exists in `stored_attestations` and that the stored account/key fields match the signer. It performs **no expiry check and no image-hash re-validation**:

```rust
// crates/contract/src/tee/tee_state.rs:469-498
pub(crate) fn is_caller_an_attested_participant(
    &self,
    participants: &Participants,
) -> Result<(), AttestationCheckError> {
    ...
    let attestation = self
        .stored_attestations
        .get(&info.tls_public_key)
        .ok_or(AttestationCheckError::AttestationNotFound)?;
    // ← no call to reverify_participants / re_verify here
    ...
    Ok(())
}
``` [2](#0-1) 

The function that *does* re-verify expiry and image-hash membership is `reverify_participants`, called only inside `verify_tee()` and `clean_invalid_attestations()`:

```rust
// crates/contract/src/tee/tee_state.rs:404-433
pub fn clean_invalid_attestations(...) {
    let has_invalid_attestation = |node_id: &NodeId| {
        !matches!(
            self.reverify_participants(node_id, tee_upgrade_deadline_duration),
            TeeQuoteStatus::Valid
        )
    };
    ...
}
``` [3](#0-2) 

`verify_tee()` is the only path that updates `accept_requests` and evicts stale entries, and it is called at most every two days by any participant: [4](#0-3) 

Every node-facing protocol method — `respond`, `vote_pk`, `vote_reshared`, `start_keygen_instance`, `start_reshare_instance` — calls `assert_caller_is_attested_participant_and_protocol_active` as its sole attestation gate: [5](#0-4) [6](#0-5) [7](#0-6) 

---

### Impact Explanation

The TEE attestation system's core safety invariant is: **only nodes running a currently-approved, unexpired TEE image may participate in signing and key-event governance**. This invariant is broken during the window between `verify_tee()` calls (up to two days per the design documentation):

- A node whose attestation has **expired** (e.g., failed to renew within 7 days) retains its stored entry and passes `is_caller_an_attested_participant`. It can call `respond()` to deliver signature outputs and `vote_pk`/`vote_reshared` to influence key-event outcomes.
- A node whose **image hash has been governance-revoked** (because a vulnerability was found in that image) similarly retains its stored entry and passes the check. The revocation intent — preventing a potentially compromised node from participating — is not enforced until the next `verify_tee()` call.

In the revoked-image-hash scenario, the node may be running a compromised TEE environment. It can still submit `respond()` calls that are accepted by the contract (the signature is verified cryptographically, but the node's key share may have been extracted from the compromised enclave), and it can cast `vote_pk`/`vote_reshared` votes that count toward threshold-based key-event completion.

This maps to the **Medium** allowed impact: *participant-state or contract execution-flow manipulation that breaks production safety/accounting invariants*.

---

### Likelihood Explanation

- Attestation expiry is a routine event (7-day TTL). Any node that goes offline or fails to renew enters the vulnerable window automatically.
- Image-hash revocation is a governance action that operators are expected to perform when a vulnerability is discovered. The gap between revocation and the next `verify_tee()` call (up to 2 days) is the exploitation window.
- No privileged access is required beyond holding a NEAR account that is a current participant with a stored (but stale) attestation entry.
- The attacker-controlled entry path is direct: call `respond()` or `vote_pk()` from the participant account after the attestation has become invalid but before `verify_tee()` runs.

---

### Recommendation

Re-verify the caller's attestation at the point of the guard, not only during periodic `verify_tee()` sweeps. Concretely, `assert_caller_is_attested_participant_and_protocol_active` (or `is_caller_an_attested_participant`) should call `reverify_participants` on the caller's stored entry and reject the call if the result is not `TeeQuoteStatus::Valid`:

```rust
fn assert_caller_is_attested_participant_and_protocol_active(&self) {
    let participants = self.protocol_state.active_participants();
    Self::assert_caller_is_signer();

    // Existing existence + key-match check
    let attestation_check = self
        .tee_state
        .is_caller_an_attested_participant(participants);
    assert_matches::assert_matches!(attestation_check, Ok(()));

    // NEW: re-verify expiry and image-hash validity inline
    let tee_upgrade_deadline = Duration::from_secs(
        self.config.tee_upgrade_deadline_duration_seconds
    );
    let caller_node_id = /* resolve from signer_id + stored entry */;
    let status = self.tee_state.reverify_participants(
        &caller_node_id,
        tee_upgrade_deadline,
    );
    assert_matches::assert_matches!(
        status,
        TeeQuoteStatus::Valid,
        "Caller attestation is no longer valid"
    );
}
```

This mirrors the fix in the reference report: move the "refresh" call before the "check" so the check always operates on current state.

---

### Proof of Concept

1. Deploy the contract with 3 participants (threshold 2). All have valid attestations.
2. Participant A submits an attestation with `expiry_timestamp_seconds = now + 5s`.
3. Advance block time by 10 seconds (past expiry). Do **not** call `verify_tee()`.
4. Participant A calls `respond(request, valid_signature)`.
5. `assert_caller_is_attested_participant_and_protocol_active` calls `is_caller_an_attested_participant`, which finds A's entry in `stored_attestations` and returns `Ok(())` — **no expiry check is performed**.
6. `accept_requests` is still `true` (only 1 of 3 attestations expired; threshold still met).
7. The `respond()` call succeeds and the signature is delivered to the user.

The same sequence applies to `vote_pk` and `vote_reshared` during a key-event, allowing a node running a revoked image to influence key-event outcomes for up to 2 days after its image hash is removed from the governance allowlist.

### Citations

**File:** crates/contract/src/lib.rs (L563-581)
```rust
    #[handle_result]
    pub fn respond(
        &mut self,
        request: SignatureRequest,
        response: dtos::SignatureResponse,
    ) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();

        log!("respond: signer={}, request={:?}", &signer, &request);

        self.assert_caller_is_attested_participant_and_protocol_active();

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }
```

**File:** crates/contract/src/lib.rs (L1078-1085)
```rust
    pub fn start_keygen_instance(&mut self, key_event_id: KeyEventId) -> Result<(), Error> {
        log!("start_keygen_instance: signer={}", env::signer_account_id(),);

        self.assert_caller_is_attested_participant_and_protocol_active();

        self.protocol_state
            .start_keygen_instance(key_event_id, self.config.key_event_timeout_blocks)
    }
```

**File:** crates/contract/src/lib.rs (L1103-1131)
```rust
    pub fn vote_pk(
        &mut self,
        key_event_id: KeyEventId,
        public_key: dtos::PublicKey,
    ) -> Result<(), Error> {
        log!(
            "vote_pk: signer={}, key_event_id={:?}, public_key={:?}",
            env::signer_account_id(),
            key_event_id,
            public_key,
        );

        self.assert_caller_is_attested_participant_and_protocol_active();

        let extended_key =
            public_key
                .try_into()
                .map_err(|err: PublicKeyExtendedConversionError| {
                    InvalidParameters::MalformedPayload {
                        reason: err.to_string(),
                    }
                })?;

        if let Some(new_state) = self.protocol_state.vote_pk(key_event_id, extended_key)? {
            self.protocol_state = new_state;
        }

        Ok(())
    }
```

**File:** crates/contract/src/lib.rs (L1693-1770)
```rust
    pub fn verify_tee(&mut self) -> Result<bool, Error> {
        log!("verify_tee: signer={}", env::signer_account_id());
        // Caller must be a participant (node or operator).
        self.voter_or_panic();
        let ProtocolContractState::Running(running_state) = &mut self.protocol_state else {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        };
        let current_params = running_state.parameters.clone();

        let tee_upgrade_deadline_duration =
            Duration::from_secs(self.config.tee_upgrade_deadline_duration_seconds);

        match self.tee_state.reverify_and_cleanup_participants(
            current_params.participants(),
            tee_upgrade_deadline_duration,
        ) {
            TeeValidationResult::Full => {
                self.accept_requests = true;
                log!("All participants have an accepted Tee status");
                Ok(true)
            }
            TeeValidationResult::Partial {
                participants_with_valid_attestation,
            } => {
                let remaining = participants_with_valid_attestation.len();
                // Defense in depth: the surviving participant set must keep the full
                // threshold relation intact — the GovernanceThreshold must still sit
                // within its bounds for the smaller set (in particular it must not
                // exceed the remaining participant count or the upper cap) and must
                // remain at least every domain's ReconstructionThreshold (the kickout
                // keeps the existing per-domain thresholds). Otherwise we refuse and
                // wait for manual intervention.
                let max_reconstruction_threshold =
                    max_reconstruction_threshold(running_state.domains.domains());
                if let Err(err) = ThresholdParameters::validate_governance_against_reconstruction(
                    u64::try_from(remaining).expect("participant count fits in u64"),
                    current_params.threshold(),
                    max_reconstruction_threshold,
                ) {
                    log!(
                        "Kicking out participants with an invalid TEE status would break the threshold relation ({:?}); {} participants remain with a valid TEE status. This requires manual intervention. We will not accept new signature requests as a safety precaution.",
                        err,
                        remaining,
                    );
                    self.accept_requests = false;
                    return Ok(false);
                }

                // here, we set it to true, because at this point, we have at least `threshold`
                // number of participants with an accepted Tee status.
                self.accept_requests = true;

                // do we want to adjust the threshold?
                //let n_participants_new = new_participants.len();
                //let new_threshold = (3 * n_participants_new + 4) / 5; // minimum 60%
                //let new_threshold = new_threshold.max(2); // but also minimum 2
                let new_threshold = usize::try_from(current_params.threshold().value())
                    .expect("threshold value fits in usize");

                let threshold_parameters = ThresholdParameters::new(
                    participants_with_valid_attestation,
                    Threshold::new(new_threshold as u64),
                )
                .expect("Require valid threshold parameters"); // this should never happen.
                current_params.validate_incoming_proposal(&threshold_parameters)?;
                // This resharing only changes the participant set, so the
                // per-domain reconstruction-threshold updates map is empty.
                let proposed_parameters =
                    ProposedThresholdParameters::new(threshold_parameters, BTreeMap::new());
                let res = running_state.transition_to_resharing_no_checks(&proposed_parameters);
                if let Some(resharing) = res {
                    self.protocol_state = ProtocolContractState::Resharing(resharing);
                }

                Ok(true)
            }
        }
    }
```

**File:** crates/contract/src/lib.rs (L2389-2403)
```rust
    fn assert_caller_is_attested_participant_and_protocol_active(&self) {
        let participants = self.protocol_state.active_participants();

        Self::assert_caller_is_signer();

        let attestation_check = self
            .tee_state
            .is_caller_an_attested_participant(participants);

        assert_matches::assert_matches!(
            attestation_check,
            Ok(()),
            "Caller must be an attested participant"
        );
    }
```

**File:** crates/contract/src/tee/tee_state.rs (L404-433)
```rust
    /// launcher-hash / measurement whitelists, or whose attestation has expired.
    /// Returns the number of entries removed.
    pub fn clean_invalid_attestations(
        &mut self,
        tee_upgrade_deadline_duration: Duration,
        max_scan: usize,
    ) -> u32 {
        let has_invalid_attestation = |node_id: &NodeId| {
            !matches!(
                self.reverify_participants(node_id, tee_upgrade_deadline_duration),
                TeeQuoteStatus::Valid
            )
        };

        // Materialize candidates before any mutation to avoid iterator invalidation.
        let invalid_tls_keys: Vec<Ed25519PublicKey> = self
            .stored_attestations
            .iter()
            .take(max_scan)
            .filter(|(_, node_attestation)| has_invalid_attestation(&node_attestation.node_id))
            .map(|(tls_pk, _)| tls_pk.clone())
            .collect();

        let removed = u32::try_from(invalid_tls_keys.len())
            .expect("u32 should always be convertible from usize on wasm32");

        for tls_pk in invalid_tls_keys {
            self.stored_attestations.remove(&tls_pk);
        }
        removed
```

**File:** crates/contract/src/tee/tee_state.rs (L469-498)
```rust
    pub(crate) fn is_caller_an_attested_participant(
        &self,
        participants: &Participants,
    ) -> Result<(), AttestationCheckError> {
        let signer_account_pk = env::signer_account_pk();
        let signer_id = env::signer_account_id();

        let info = participants
            .info(&signer_id)
            .ok_or(AttestationCheckError::CallerNotParticipant)?;

        let attestation = self
            .stored_attestations
            .get(&info.tls_public_key)
            .ok_or(AttestationCheckError::AttestationNotFound)?;

        if attestation.node_id.account_id != signer_id {
            return Err(AttestationCheckError::AttestationOwnerMismatch);
        }

        // Stored account keys are Ed25519 by construction; a non-Ed25519
        // signer necessarily mismatches.
        let signer_ed25519 = Ed25519PublicKey::try_from(&signer_account_pk)
            .map_err(|_| AttestationCheckError::AttestationKeyMismatch)?;
        if attestation.node_id.account_public_key != signer_ed25519 {
            return Err(AttestationCheckError::AttestationKeyMismatch);
        }

        Ok(())
    }
```
