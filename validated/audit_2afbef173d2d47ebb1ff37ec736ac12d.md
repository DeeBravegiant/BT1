### Title
Missing Attestation Re-Verification in `is_caller_an_attested_participant` Allows Participants with Expired/Revoked TEE Attestations to Vote on Key Events - (File: `crates/contract/src/tee/tee_state.rs`)

---

### Summary

`is_caller_an_attested_participant` in `TeeState` checks only that a stored attestation **exists** for the caller's TLS key, but never re-verifies whether that attestation is still **valid** (not expired, docker image hash still whitelisted, OS measurements still accepted). This is the direct analog of the Ethos `verifiedProfileIdForAddress` missing the `isAddressCompromised` check: a revocation state exists in the system but is not consulted at the authorization gate.

---

### Finding Description

`is_caller_an_attested_participant` is the central authorization guard used by `assert_caller_is_attested_participant_and_protocol_active`, which gates every sensitive node-facing endpoint: `respond`, `respond_ckd`, `respond_verify_foreign_tx`, `vote_pk`, `vote_reshared`, `vote_abort_key_event_instance`, `start_keygen_instance`, and `start_reshare_instance`. [1](#0-0) 

The function performs four checks:
1. Caller is in the active `Participants` set.
2. A `NodeAttestation` entry exists in `stored_attestations` keyed by the caller's TLS public key.
3. The stored `account_id` matches the caller.
4. The stored `account_public_key` matches the caller's signing key.

It does **not** call `reverify_participants`, which is the function that actually re-checks certificate expiry, docker image hash allowlist, launcher compose hash allowlist, and OS measurement allowlist: [2](#0-1) 

A separate cleanup path, `reverify_and_cleanup_participants`, exists and is called during `vote_new_parameters` to validate proposed participants, but it is never invoked inside the per-call authorization gate: [3](#0-2) 

The cleanup of stale attestations from `stored_attestations` is handled by the separate `clean_invalid_attestations` endpoint, which must be triggered explicitly and is not called atomically with any revocation event: [4](#0-3) 

The test `clean_tee_status__should_not_touch_attestations` explicitly confirms that `clean_tee_status` leaves `stored_attestations` unchanged — only vote maps are cleaned — meaning a revoked participant's attestation entry persists in storage until `clean_invalid_attestations` is separately invoked. [5](#0-4) 

The `assert_caller_is_attested_participant_and_protocol_active` guard is used by `vote_pk`, `vote_reshared`, `start_keygen_instance`, and `start_reshare_instance`: [6](#0-5) 

---

### Impact Explanation

A participant whose TEE attestation has become invalid — because their docker image hash was removed via `vote_remove_code_hash`, their certificate expired, or their OS measurement was removed — retains a stale entry in `stored_attestations`. Until `clean_invalid_attestations` is explicitly called, `is_caller_an_attested_participant` returns `Ok(())` for that participant, because it only checks existence, not validity.

This allows the participant (potentially running untrusted or compromised software) to:

- Call `vote_pk` to cast a vote during distributed key generation.
- Call `vote_reshared` to cast a vote during key resharing.
- Call `start_keygen_instance` or `start_reshare_instance` if they hold the leader position (lowest participant ID).

These are the operations that determine which public key the MPC network adopts and which participant set holds the new key shares. A participant running a non-whitelisted (potentially malicious) image influencing these votes breaks the core TEE safety invariant: that only nodes running verified, approved software can participate in key events.

The `accept_requests` flag blocks `respond*` callbacks but is **not** checked in `vote_pk`, `vote_reshared`, or the `start_*` functions, so even if the contract's liveness guard is triggered, key-event voting remains open to the revoked participant.

This maps to: **Medium — participant-state and contract execution-flow manipulation that breaks production safety/accounting invariants.**

---

### Likelihood Explanation

The attack window opens whenever any of the following occurs:
- A docker image hash is removed via governance (`vote_remove_code_hash`).
- A launcher hash is removed via `vote_remove_launcher_hash`.
- An OS measurement is removed via `vote_remove_os_measurement`.
- A participant's attestation certificate expires naturally.

In all cases, `clean_invalid_attestations` must be called separately to evict the stale entry. This is an explicit, manual operation with no automatic trigger. The window between revocation and cleanup is unbounded in the protocol design. During this window, the revoked participant can call `vote_reshared` or `vote_pk` without restriction.

---

### Recommendation

Add an inline re-verification call inside `is_caller_an_attested_participant` after confirming the attestation exists:

```rust
pub(crate) fn is_caller_an_attested_participant(
    &self,
    participants: &Participants,
    tee_upgrade_deadline_duration: Duration,  // add parameter
) -> Result<(), AttestationCheckError> {
    // ... existing checks ...

    // Re-verify the attestation is still valid (not expired, image hash still allowed, etc.)
    let tee_status = self.reverify_participants(&attestation.node_id, tee_upgrade_deadline_duration);
    if !matches!(tee_status, TeeQuoteStatus::Valid) {
        return Err(AttestationCheckError::AttestationNotFound); // or a new variant
    }

    Ok(())
}
```

Alternatively, `assert_caller_is_attested_participant_and_protocol_active` should call `reverify_participants` before delegating to `is_caller_an_attested_participant`, so that every key-event vote and respond callback is gated on a live attestation check, not just a stored-entry existence check.

---

### Proof of Concept

1. Contract is `Running` with participants `[P1, P2, P3]`, threshold 2. All have valid stored attestations.
2. Governance votes remove P3's docker image hash via `vote_remove_code_hash`. P3 is now running non-whitelisted software.
3. `clean_tee_status` is called. `stored_attestations` is **unchanged** (confirmed by test). P3's entry remains.
4. A resharing is proposed to remove P3 from the next epoch. The contract enters `Resharing` state. `active_participants()` during resharing returns the **new proposed set** — if P3 is excluded from the proposal, they cannot vote. However, if the resharing proposal still includes P3 (e.g., only changing threshold), P3 remains in `active_participants()`.
5. P3 calls `vote_reshared` with a valid `key_event_id`. `is_caller_an_attested_participant` finds P3 in participants and finds P3's stale attestation in `stored_attestations`. It returns `Ok(())` without calling `reverify_participants`. The vote is counted.
6. P3 is running non-whitelisted (potentially malicious) software and has successfully influenced a key-event vote, violating the TEE safety invariant. [1](#0-0) [7](#0-6) [8](#0-7)

### Citations

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

**File:** crates/contract/src/tee/tee_state.rs (L436-443)
```rust
    /// Returns the list of accounts that currently have TEE attestations stored.
    /// Note: This may include accounts that are no longer active protocol participants.
    pub fn get_tee_accounts(&self) -> Vec<NodeId> {
        self.stored_attestations
            .values()
            .map(|node_attestation| node_attestation.node_id.clone())
            .collect()
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

**File:** crates/contract/src/lib.rs (L564-573)
```rust
    pub fn respond(
        &mut self,
        request: SignatureRequest,
        response: dtos::SignatureResponse,
    ) -> Result<(), Error> {
        let signer = Self::assert_caller_is_signer();

        log!("respond: signer={}, request={:?}", &signer, &request);

        self.assert_caller_is_attested_participant_and_protocol_active();
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

**File:** crates/contract/src/lib.rs (L2415-2422)
```rust
    ///   `vote_cancel_keygen`, `register_foreign_chain_support`, `submit_participant_info`,
    ///   and the node-migration methods.
    /// - Via [`Self::voter_or_panic`]: `propose_update`, `vote_update`, `remove_update_vote`,
    ///   `vote_code_hash`, the launcher/OS-measurement votes,
    ///   `vote_update_foreign_chain_providers`, and `verify_tee`.
    /// - Via [`Self::assert_caller_is_attested_participant_and_protocol_active`]: the key-event
    ///   votes `vote_pk`, `vote_reshared`, `vote_abort_key_event_instance`, and the leader-only
    ///   `start_keygen_instance` / `start_reshare_instance`, plus the `respond*` callbacks.
```
