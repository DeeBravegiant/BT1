### Title
`is_caller_an_attested_participant` Skips Attestation Re-Verification, Allowing Nodes with Expired or Revoked TEE Attestations to Participate in Signing - (File: `crates/contract/src/tee/tee_state.rs`)

---

### Summary

The contract's internal caller-authentication check (`is_caller_an_attested_participant`) only verifies that a stored attestation *exists* and matches the caller's identity. It does not call `reverify_participants` to confirm the attestation is still valid at the current block time. As a result, a node whose attestation has expired or whose image hash has been revoked can continue to call `respond`, `vote_pk`, `vote_reshared`, `start_keygen_instance`, `start_reshare_instance`, and `vote_abort_key_event_instance` — bypassing the TEE enforcement invariant — until `verify_tee` is explicitly invoked by a participant.

---

### Finding Description

`TeeState::is_caller_an_attested_participant` (the "view" check) and `TeeState::reverify_participants` (the "full" check) diverge in exactly the same way as Oracle's `viewPrice` and `getPrice`:

**`is_caller_an_attested_participant`** (lines 469–498 of `tee_state.rs`) checks only:
1. Is the signer in the active participants list?
2. Is there a stored attestation keyed by the signer's TLS public key?
3. Does the stored `account_id` and `account_public_key` match the signer?

It does **not** call `re_verify` on the stored `VerifiedAttestation`.

**`reverify_participants`** (lines 206–232 of `tee_state.rs`) additionally calls `verified_attestation.re_verify(...)`, which checks:
- Has the attestation's `expiry_timestamp_seconds` passed?
- Is the node's MPC image hash still in the current allowed list?
- Is the launcher compose hash still in the current allowed list?
- Are the OS measurements still in the allowed list?

`assert_caller_is_attested_participant_and_protocol_active` — called at the top of every node-facing write method (`respond`, `vote_pk`, `vote_reshared`, `start_keygen_instance`, `start_reshare_instance`, `vote_abort_key_event_instance`) — routes through `is_caller_an_attested_participant`, not through `reverify_participants`. The full re-verification path is only exercised lazily when `verify_tee` is explicitly called. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

---

### Impact Explanation

After a node's attestation expires **or** after its MPC image hash's grace period lapses (i.e., `allowed_docker_image_hashes` no longer contains the hash the node attested against), the node can still:

- Call `respond` / `respond_ckd` / `respond_verify_foreign_tx` and have its signatures accepted by the contract.
- Call `vote_pk` and `vote_reshared`, influencing distributed key generation and resharing outcomes.
- Call `start_keygen_instance` / `start_reshare_instance`, controlling when those protocol phases begin.

The contract's TEE enforcement invariant — *only nodes running currently-approved, non-expired TEE images may participate in the signing protocol* — is broken for the entire window between when an attestation becomes invalid and when `verify_tee` is next called. `verify_tee` is not triggered automatically; it requires an explicit participant call.

This maps to the **Medium** allowed impact: *participant-state or contract execution-flow manipulation that breaks production safety/accounting invariants without relying on network-level DoS or operator misconfiguration.* [6](#0-5) [7](#0-6) 

---

### Likelihood Explanation

Attestations carry a finite `expiry_timestamp_seconds` (currently `DEFAULT_EXPIRATION_DURATION_SECONDS`, approximately 7 days for Dstack attestations). Image hash grace periods are also finite and configurable. Both conditions arise in normal operation:

- Every node must periodically resubmit its attestation; a missed resubmission leaves an expired entry in `stored_attestations`.
- When operators vote in a new MPC image hash, the old hash's grace period eventually lapses.

In either case, `is_caller_an_attested_participant` continues to return `Ok(())` for the affected node because it never calls `re_verify`. The node can exploit this window without any special privilege — it only needs its existing NEAR account key, which it already uses to sign every node-API call. [8](#0-7) [9](#0-8) 

---

### Recommendation

Apply the same fix pattern as the Oracle report's mitigation: make the "view" check consistent with the "full" check by incorporating `reverify_participants` into `is_caller_an_attested_participant` (or into `assert_caller_is_attested_participant_and_protocol_active`):

```rust
// In is_caller_an_attested_participant (or its call site):
let tee_status = self.reverify_participants(&attestation.node_id, tee_upgrade_deadline_duration);
if !matches!(tee_status, TeeQuoteStatus::Valid) {
    return Err(AttestationCheckError::AttestationExpiredOrRevoked);
}
```

This ensures that every node-API write call enforces the same attestation-validity invariant that `verify_tee` enforces, eliminating the window during which a node with an expired or revoked attestation can participate. [2](#0-1) 

---

### Proof of Concept

1. At time T=0, node `alice.near` submits a valid attestation tied to image hash `H_v1`. `stored_attestations[alice_tls_key]` is populated.
2. At time T=1, participants vote in a new image hash `H_v2`. The grace period for `H_v1` is set to 15 seconds.
3. At time T=16, `H_v1` is no longer in `allowed_docker_image_hashes`. `reverify_participants(&alice_node_id, grace_period)` now returns `TeeQuoteStatus::Invalid`.
4. At time T=16, `alice.near` calls `respond(request, signature)`. `assert_caller_is_attested_participant_and_protocol_active` calls `is_caller_an_attested_participant`, which finds `alice_tls_key` in `stored_attestations`, confirms `account_id` and `account_public_key` match, and returns `Ok(())` — **without calling `re_verify`**.
5. The signature passes mathematical verification and is accepted by the contract.
6. `alice.near` — running the revoked image `H_v1` — has successfully participated in the signing protocol, violating the TEE enforcement invariant.

`verify_tee` has not been called between T=1 and T=16, so `accept_requests` is still `true` and the `alice.near` node is still in the active participant set. [1](#0-0) [10](#0-9) [11](#0-10)

### Citations

**File:** crates/contract/src/tee/tee_state.rs (L145-148)
```rust
    fn current_time_seconds() -> u64 {
        let current_time_milliseconds = env::block_timestamp_ms();
        current_time_milliseconds / 1_000
    }
```

**File:** crates/contract/src/tee/tee_state.rs (L206-232)
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

**File:** crates/contract/src/tee/tee_state.rs (L287-303)
```rust
    pub fn get_allowed_mpc_docker_image_hashes(
        &self,
        tee_upgrade_deadline_duration: Duration,
    ) -> Vec<NodeImageHash> {
        self.get_allowed_mpc_docker_images(tee_upgrade_deadline_duration)
            .into_iter()
            .map(|entry| entry.image_hash)
            .collect()
    }

    pub fn get_allowed_mpc_docker_images(
        &self,
        tee_upgrade_deadline_duration: Duration,
    ) -> Vec<AllowedMpcDockerImage> {
        self.allowed_docker_image_hashes
            .get(tee_upgrade_deadline_duration)
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

**File:** crates/contract/src/lib.rs (L563-651)
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

        let domain = request.domain_id;
        let public_key = self.public_key_extended(domain)?;

        let signature_is_valid = match (&response, public_key) {
            (
                dtos::SignatureResponse::Secp256k1(signature_response),
                PublicKeyExtended::Secp256k1 { near_public_key },
            ) => {
                // generate the expected public key
                let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)
                    .expect("Secp256k1 variant always has a secp256k1 key");
                let affine = *k256::PublicKey::try_from(&secp_pk)
                    .expect("stored key is always valid")
                    .as_affine();
                let expected_public_key =
                    derive_key_secp256k1(&affine, &request.tweak).map_err(RespondError::from)?;

                let payload_hash = request.payload.as_ecdsa().expect("Payload is not ECDSA");

                // Check the signature is correct
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    payload_hash,
                    &expected_public_key,
                )
                .is_ok()
            }
            (
                dtos::SignatureResponse::Ed25519 { signature },
                PublicKeyExtended::Ed25519 {
                    edwards_point: public_key_edwards_point,
                    ..
                },
            ) => {
                let derived_public_key_edwards_point = derive_public_key_edwards_point_ed25519(
                    &public_key_edwards_point,
                    &request.tweak,
                );
                let derived_public_key_32_bytes =
                    dtos::Ed25519PublicKey::from(derived_public_key_edwards_point.compress());

                let message = request.payload.as_eddsa().expect("Payload is not EdDSA");

                near_mpc_signature_verifier::verify_eddsa_signature(
                    signature,
                    message,
                    &derived_public_key_32_bytes,
                )
                .is_ok()
            }
            (signature_response, public_key_requested) => {
                return Err(RespondError::SignatureSchemeMismatch {
                    mpc_scheme: Box::new(signature_response.clone()),
                    user_scheme: Box::new(public_key_requested),
                }
                .into());
            }
        };

        if !signature_is_valid {
            return Err(RespondError::InvalidSignature.into());
        }

        pending_requests::resolve_yields_for(
            &mut self.pending_signature_requests,
            &request,
            serde_json::to_vec(&response).unwrap(),
        )
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

**File:** crates/contract/src/lib.rs (L1161-1175)
```rust
    pub fn vote_reshared(&mut self, key_event_id: KeyEventId) -> Result<(), Error> {
        log!(
            "vote_reshared: signer={}, resharing_id={:?}",
            env::signer_account_id(),
            key_event_id,
        );

        self.assert_caller_is_attested_participant_and_protocol_active();

        if let Some(new_state) = self.protocol_state.vote_reshared(key_event_id)? {
            // Resharing has concluded, transition to running state
            self.protocol_state = new_state;
            self.recompute_available_foreign_chains();

            // Spawn a promise to clean up votes from non-participants.
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

**File:** crates/mpc-attestation/src/attestation.rs (L214-255)
```rust
impl VerifiedAttestation {
    pub fn re_verify(
        &self,
        timestamp_seconds: u64,
        allowed_mpc_docker_image_hashes: &[NodeImageHash],
        allowed_launcher_docker_compose_hashes: &[LauncherDockerComposeHash],
        allowed_measurements: &[ExpectedMeasurements],
    ) -> Result<(), VerificationError> {
        match self {
            Self::Dstack(ValidatedDstackAttestation {
                mpc_image_hash,
                launcher_compose_hash,
                expiry_timestamp_seconds: expiration_timestamp_seconds,
                measurements,
            }) => {
                let attestation_has_expired = *expiration_timestamp_seconds < timestamp_seconds;

                if attestation_has_expired {
                    return Err(VerificationError::Custom(format!(
                        "The attestation expired at t = {:?}, time_now = {:?}",
                        expiration_timestamp_seconds, timestamp_seconds
                    )));
                }

                let () = verify_mpc_hash(mpc_image_hash, allowed_mpc_docker_image_hashes)?;
                let () = verify_launcher_compose_hash(
                    launcher_compose_hash,
                    allowed_launcher_docker_compose_hashes,
                )?;

                verify_measurements(measurements, allowed_measurements)?;

                Ok(())
            }
            Self::Mock(mock_attestation) => mock_attestation.verify_constraints(
                timestamp_seconds,
                allowed_mpc_docker_image_hashes,
                allowed_launcher_docker_compose_hashes,
                allowed_measurements,
            ),
        }
    }
```
