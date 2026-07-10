### Title
Stale TEE Attestation State in `is_caller_an_attested_participant` Allows Nodes with Expired or Revoked Attestations to Submit Signatures — (File: `crates/contract/src/tee/tee_state.rs`)

---

### Summary

The `is_caller_an_attested_participant` function, called on every `respond()` and `respond_ckd()` invocation, checks only that a stored attestation *exists* and that the account identity matches. It does **not** re-verify whether the attestation is still valid (i.e., not expired, image hash still on the allowed list, measurements still accepted). Attestation validity is only re-checked when `verify_tee()` is called — a periodic, externally-triggered operation scheduled every two days. In the window between `verify_tee()` calls, a participant whose TEE attestation has expired or whose image hash has been revoked can continue calling `respond()` and `respond_ckd()`, bypassing the TEE enforcement invariant that only nodes running approved code may participate in threshold signing and confidential key derivation.

---

### Finding Description

`is_caller_an_attested_participant` in `crates/contract/src/tee/tee_state.rs` performs three checks:

1. The caller's account ID is present in the active `Participants` set.
2. A `NodeAttestation` entry exists in `stored_attestations` keyed by the participant's TLS public key.
3. The stored `account_id` and `account_public_key` match the transaction signer. [1](#0-0) 

None of these checks call `re_verify()` on the stored `VerifiedAttestation`. The expiry timestamp, the allowed MPC docker image hash, the allowed launcher compose hash, and the accepted measurements are **never consulted** at this point.

The function that does perform those checks is `reverify_participants()`: [2](#0-1) 

`reverify_participants()` is only reachable through `reverify_and_cleanup_participants()`, which is only called from `verify_tee()`: [3](#0-2) 

`verify_tee()` is a separate, externally-triggered call. According to the design documentation, it is scheduled every two days: [4](#0-3) 

Both `respond()` and `respond_ckd()` gate on `assert_caller_is_attested_participant_and_protocol_active()`, which delegates to `is_caller_an_attested_participant` — the stale check: [5](#0-4) [6](#0-5) [7](#0-6) 

The `accept_requests` flag provides a coarse global gate, but it is only updated by `verify_tee()`. Between calls it remains `true` even if individual participants' attestations have since expired or been revoked.

---

### Impact Explanation

The TEE enforcement model requires that only nodes running a governance-approved image inside a genuine TDX enclave may participate in threshold signing and CKD. When a security vulnerability is found in an MPC image, governance participants vote to remove that image hash from the allowed list. The expectation is that nodes running the vulnerable image are immediately locked out of `respond()` and `respond_ckd()`.

Because `is_caller_an_attested_participant` does not re-verify the stored attestation's validity, a node whose image hash has just been revoked can continue calling `respond()` and `respond_ckd()` for up to two days — the full interval until the next `verify_tee()` call. During this window, a node running the revoked (potentially compromised) image participates in live threshold signing and confidential key derivation operations. If the image contains a vulnerability that enables key-share exfiltration, the attacker has a two-day window to exploit it while still being treated as a fully authorized participant by the contract. This breaks the production safety invariant that TEE enforcement is supposed to provide: only nodes running approved code may access signing capability and key-share material.

**Impact**: Medium — participant-state and contract execution-flow invariant broken; a node with a revoked attestation retains signing and CKD participation rights, enabling unauthorized access to signing capability and key-share material within the revocation window.

---

### Likelihood Explanation

The scenario requires: (a) a governance vote to remove an image hash (a realistic operational event during any security patch cycle), and (b) a malicious or compromised operator who continues running the revoked image and does not voluntarily stop. The two-day `verify_tee()` interval is a documented operational parameter, not an edge case. The attacker-controlled entry path — calling `respond()` or `respond_ckd()` from a NEAR account that is still in the active participants list — is fully permissionless once the account is a participant.

**Likelihood**: Low — requires a participant to be actively adversarial after image-hash revocation, but the window is structurally guaranteed by the two-day scheduling interval.

---

### Recommendation

Add a per-call attestation validity check inside `is_caller_an_attested_participant` (or inside `assert_caller_is_attested_participant_and_protocol_active`) that calls `reverify_participants()` for the specific caller before allowing `respond()` or `respond_ckd()` to proceed. This mirrors the fix described in the external report: re-verify the authentication state at the time of each sensitive operation, not only during periodic background sweeps. If the gas cost of full re-verification on every call is prohibitive, a minimum viable fix is to check the attestation's expiry timestamp inline, deferring the image-hash check to `verify_tee()` while still blocking calls from nodes whose attestations have expired.

---

### Proof of Concept

1. Deploy the MPC contract with participants P1, P2, P3 (threshold 2). All submit valid attestations with image hash H.
2. Governance votes to remove H from the allowed list (`vote_for_hash` with a new hash H2).
3. P3 does **not** resubmit an attestation with H2; its stored attestation now references a revoked image hash.
4. Do **not** call `verify_tee()` yet (simulating the up-to-two-day window).
5. A user calls `sign()` on the contract.
6. P3 calls `respond()` with a valid signature. The call succeeds: `is_caller_an_attested_participant` finds P3's stored attestation, confirms account identity, and returns `Ok(())` — it never calls `re_verify()` and never checks whether H is still on the allowed list.
7. The signature is accepted and the yield is resolved, despite P3 running a revoked image.
8. Repeat for `respond_ckd()` to confirm the same bypass applies to confidential key derivation. [1](#0-0) [2](#0-1) [8](#0-7) [7](#0-6)

### Citations

**File:** crates/contract/src/tee/tee_state.rs (L205-232)
```rust
    /// reverifies stored participant attestations.
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

**File:** crates/contract/src/lib.rs (L653-667)
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

**File:** crates/contract/src/lib.rs (L1693-1769)
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

**File:** docs/tee-lifecycle.md (L208-208)
```markdown
4. **Collective verification** — every 2 days, any participant can trigger `verify_tee()` on the governance contract to re-validate all stored attestations and evict nodes whose image hashes are no longer on the approved list.
```
