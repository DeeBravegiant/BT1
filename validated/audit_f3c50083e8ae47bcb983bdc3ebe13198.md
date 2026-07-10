### Title
Expired TEE Attestation Not Checked at Point-of-Use Allows Participation in Signing and Governance Despite Lapsed TEE Validity - (File: crates/contract/src/tee/tee_state.rs)

---

### Summary

`is_caller_an_attested_participant` in `TeeState` verifies that a stored attestation *exists* and that the stored keys match the caller, but never checks whether the attestation has **expired**. Every privileged node-facing method that gates access through `assert_caller_is_attested_participant_and_protocol_active` — including `respond`, `respond_ckd`, `respond_verify_foreign_tx`, `start_reshare_instance`, and `vote_reshared` — therefore accepts calls from participants whose TEE attestation has lapsed, bypassing the TEE validity invariant that is enforced only during `vote_new_parameters` and `verify_tee`.

---

### Finding Description

`is_caller_an_attested_participant` performs four checks:

1. The signer is in the active participants list.
2. A stored attestation exists for the participant's TLS key.
3. The attestation's `account_id` matches the signer.
4. The attestation's `account_public_key` matches the signer's signing key. [1](#0-0) 

It does **not** call `re_verify` with the current block timestamp. The expiry check exists in `reverify_participants`: [2](#0-1) 

but `reverify_participants` is only reachable through `reverify_and_cleanup_participants`, which is only called from `vote_new_parameters` and `verify_tee`. [3](#0-2) 

`assert_caller_is_attested_participant_and_protocol_active` delegates entirely to `is_caller_an_attested_participant`: [4](#0-3) 

This guard is the sole TEE check in all five privileged node methods:

- `respond` / `respond_ckd` / `respond_verify_foreign_tx` — submit signature, CKD, and foreign-tx verification responses
- `start_reshare_instance` — initiates a resharing attempt (leader-only)
- `vote_reshared` — casts a vote that can complete a resharing epoch [5](#0-4) [6](#0-5) [7](#0-6) 

The design document states nodes must renew attestations every 7 days and call `verify_tee` on the same cadence. Between an attestation's expiry and the next successful `verify_tee` invocation, the contract has no mechanism to block the expired node from submitting responses or governance votes. [8](#0-7) 

---

### Impact Explanation

The TEE attestation is the contract's only on-chain proof that a node is running inside a trusted execution environment with its key share protected. An expired attestation means the node's TEE status is unverified: the node may have been rebooted outside the TEE, potentially exposing key material to the operator.

During the expiry window a node with a lapsed attestation can:

- Submit `respond` / `respond_ckd` / `respond_verify_foreign_tx` calls that the contract accepts as coming from a "trusted" TEE participant, even though the TEE guarantee has lapsed.
- Cast `vote_reshared` votes that count toward completing a resharing epoch, influencing which participant set takes over key custody.
- Call `start_reshare_instance` if it holds the lowest participant ID, controlling the timing of resharing attempts.

This breaks the production safety invariant that only nodes with currently-valid TEE attestations may participate in signing and governance, matching the **Medium** impact tier: *participant-state and contract execution-flow manipulation that breaks production safety/accounting invariants*.

---

### Likelihood Explanation

Attestations have a finite validity period. A node that fails to renew (network partition, crash, deliberate delay) will have an expired attestation. `verify_tee` is called by the nodes themselves on a 7-day cadence; an adversarial or faulty node can simply not call it, extending the window indefinitely. No privileged operator action is required — the expired node itself triggers the bypass by calling `respond` or `vote_reshared` directly.

---

### Recommendation

Add an expiry check inside `is_caller_an_attested_participant` by invoking `re_verify` with `Self::current_time_seconds()` (the same helper used in `reverify_participants`), or delegate to `reverify_participants` directly. This ensures that every call to `respond`, `respond_ckd`, `respond_verify_foreign_tx`, `start_reshare_instance`, and `vote_reshared` is gated on a currently-valid attestation, not merely a stored one.

---

### Proof of Concept

```
1. Participant P submits a valid attestation with expiry_timestamp = T via submit_participant_info.
   → stored_attestations[P.tls_key] is populated; attestation passes re_verify at time < T.

2. Block timestamp advances past T (attestation expires).
   → verify_tee has not been called yet; stored_attestations[P.tls_key] still present.

3. P calls respond(request, valid_signature).
   → assert_caller_is_attested_participant_and_protocol_active() fires.
   → is_caller_an_attested_participant() finds the stored entry, verifies key match → Ok(()).
   → No expiry check is performed.
   → Contract accepts the response and resolves the pending yield.

4. P's TEE may be compromised (key share accessible), yet its response is treated as
   coming from a trusted TEE participant.
```

The same path applies to `respond_ckd`, `respond_verify_foreign_tx`, `start_reshare_instance`, and `vote_reshared`.

### Citations

**File:** crates/contract/src/tee/tee_state.rs (L206-231)
```rust
    pub(crate) fn reverify_participants(
        &self,
        node_id: &NodeId,
        tee_upgrade_deadline_duration: Duration,
    ) -> TeeQuoteStatus {
        let allowed_mpc_docker_image_hashes =
            self.get_allowed_mpc_docker_image_hashes(tee_upgrade_deadline_duration);
        let allowed_launcher_compose_hashes = self.get_allowed_launcher_compose_hashes();
        let allowed_measurements = self.get_accepted_measurements();

        let participant_attestation = self.stored_attestations.get(&node_id.tls_public_key);
        let Some(participant_attestation) = participant_attestation else {
            return TeeQuoteStatus::Invalid("participant has no attestation".to_string());
        };

        // Verify the attestation quote
        let time_stamp_seconds = Self::current_time_seconds();
        match participant_attestation.verified_attestation.re_verify(
            time_stamp_seconds,
            &allowed_mpc_docker_image_hashes,
            &allowed_launcher_compose_hashes,
            &allowed_measurements,
        ) {
            Ok(()) => TeeQuoteStatus::Valid,
            Err(err) => TeeQuoteStatus::Invalid(err.to_string()),
        }
```

**File:** crates/contract/src/tee/tee_state.rs (L238-277)
```rust
    pub fn reverify_and_cleanup_participants(
        &mut self,
        participants: &Participants,
        tee_upgrade_deadline_duration: Duration,
    ) -> TeeValidationResult {
        self.allowed_docker_image_hashes
            .cleanup_expired_hashes(tee_upgrade_deadline_duration);

        let participants_with_valid_attestation: Vec<_> = participants
            .participants()
            .iter()
            .filter(|(_, _, participant_info)| {
                // Use the stored NodeId (keyed by TLS public key) so the real
                // `account_public_key` participates in re-verification. If
                // there is no stored attestation for this TLS key, the
                // participant is invalid.
                let Some(node_id) = self.find_node_id_by_tls_key(&participant_info.tls_public_key)
                else {
                    return false;
                };

                let tee_status =
                    self.reverify_participants(&node_id, tee_upgrade_deadline_duration);

                matches!(tee_status, TeeQuoteStatus::Valid)
            })
            .cloned()
            .collect();

        if participants_with_valid_attestation.len() != participants.len() {
            let participants_with_valid_attestation =
                Participants::init(participants.next_id(), participants_with_valid_attestation);

            TeeValidationResult::Partial {
                participants_with_valid_attestation,
            }
        } else {
            TeeValidationResult::Full
        }
    }
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

**File:** crates/contract/src/lib.rs (L653-666)
```rust
    #[handle_result]
    pub fn respond_ckd(&mut self, request: CKDRequest, response: CKDResponse) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();
        log!("respond_ckd: signer={}, request={:?}", &signer, &request);

        if !self.protocol_state.is_running_or_resharing() {
            return Err(InvalidState::ProtocolStateNotRunning.into());
        }

        if !self.accept_requests {
            return Err(TeeError::TeeValidationFailed.into());
        }

        self.assert_caller_is_attested_participant_and_protocol_active();
```

**File:** crates/contract/src/lib.rs (L886-938)
```rust
    pub fn vote_new_parameters(
        &mut self,
        prospective_epoch_id: EpochId,
        proposal: dtos::ProposedThresholdParameters,
    ) -> Result<(), Error> {
        Self::assert_caller_is_signer();
        let proposal: ProposedThresholdParameters = proposal.try_into_contract_type()?;
        log!(
            "vote_new_parameters: signer={}, proposal={:?}",
            env::signer_account_id(),
            proposal,
        );

        let tee_upgrade_deadline_duration =
            Duration::from_secs(self.config.tee_upgrade_deadline_duration_seconds);

        let validation_result = self.tee_state.reverify_and_cleanup_participants(
            proposal.participants(),
            tee_upgrade_deadline_duration,
        );

        let proposed_participants = proposal.participants();
        match validation_result {
            TeeValidationResult::Full => {
                if let Some(new_state) = self
                    .protocol_state
                    .vote_new_parameters(prospective_epoch_id, &proposal)?
                {
                    self.protocol_state = new_state;
                }
                Ok(())
            }
            TeeValidationResult::Partial {
                participants_with_valid_attestation,
            } => {
                let invalid_participants: Vec<_> = proposed_participants
                    .participants()
                    .iter()
                    .filter(|(account_id, _, _)| {
                        !participants_with_valid_attestation
                            .is_participant_given_account_id(account_id)
                    })
                    .collect();

                Err(InvalidParameters::InvalidTeeRemoteAttestation {
                    reason: format!(
                        "The following participants have invalid TEE status: {:?}",
                        invalid_participants
                    ),
                }
                .into())
            }
        }
```

**File:** crates/contract/src/lib.rs (L1133-1145)
```rust
    /// Starts a new attempt to reshare the key for the current domain.
    /// This only succeeds if the signer is the leader (the participant with the lowest ID).
    #[handle_result]
    pub fn start_reshare_instance(&mut self, key_event_id: KeyEventId) -> Result<(), Error> {
        log!(
            "start_reshare_instance: signer={}",
            env::signer_account_id()
        );

        self.assert_caller_is_attested_participant_and_protocol_active();
        self.protocol_state
            .start_reshare_instance(key_event_id, self.config.key_event_timeout_blocks)
    }
```

**File:** crates/contract/src/lib.rs (L1161-1168)
```rust
    pub fn vote_reshared(&mut self, key_event_id: KeyEventId) -> Result<(), Error> {
        log!(
            "vote_reshared: signer={}, resharing_id={:?}",
            env::signer_account_id(),
            key_event_id,
        );

        self.assert_caller_is_attested_participant_and_protocol_active();
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
