### Title
Stale Stored Attestation Used for Participant Authorization Without Re-Verification - (`File: crates/contract/src/tee/tee_state.rs`)

### Summary

`TeeState::is_caller_an_attested_participant` checks only that an attestation *exists* in `stored_attestations` and that the stored keys match the caller's signing key. It never calls `reverify_participants` to confirm the stored attestation is still currently valid (not expired, still tied to an allowed docker image hash, launcher hash, or OS measurement). This is the direct analog of the Aloe `borrowBalanceStored` bug: a cached value recorded at submission time is used for a security-critical authorization check instead of the current, re-evaluated value.

### Finding Description

`is_caller_an_attested_participant` performs three checks:

1. The caller's account ID is in the active participant set.
2. A `NodeAttestation` entry exists in `stored_attestations` keyed by the participant's TLS public key.
3. The stored `account_id` and `account_public_key` match the transaction signer. [1](#0-0) 

It does **not** call `reverify_participants`, which is the function that re-runs `re_verify` against the current block timestamp, the current allowed docker image hash list, the current launcher compose hash list, and the current OS measurement list: [2](#0-1) 

`assert_caller_is_attested_participant_and_protocol_active` (which wraps `is_caller_an_attested_participant`) is the gate for every sensitive node-facing method:

- `respond` — submits a threshold signature to the contract [3](#0-2) 
- `vote_pk` — casts a DKG public-key vote [4](#0-3) 
- `vote_reshared` — votes to conclude a resharing epoch [5](#0-4) 
- `start_keygen_instance` / `start_reshare_instance` / `vote_abort_key_event_instance` [6](#0-5) 

The `stored_attestations` map is populated at the time a node calls `submit_participant_info`. The `VerifiedAttestation` stored there carries an expiry timestamp and is tied to the docker image hash and launcher hash that were in the allowed set at submission time. After submission, the allowed sets can change (governance votes can remove a docker image hash via `vote_code_hash` / `whitelist_tee_proposal`, or a launcher hash via `vote_remove_launcher_hash`), and the attestation's own certificate can expire. None of these post-submission changes are reflected in the authorization check. [7](#0-6) 

The only cleanup paths are:
- `clean_invalid_attestations`, called lazily after resharing with a bounded scan of 100 entries. [8](#0-7) 
- `reverify_and_cleanup_participants`, called only inside `vote_new_parameters`. [9](#0-8) 

Between these events, a node whose attestation has expired or whose docker image hash has been revoked retains full authorization to call `respond`, `vote_pk`, and `vote_reshared`.

### Impact Explanation

A node running a docker image whose hash has been removed from the allowed set (e.g., because a critical vulnerability was disclosed) can still call `respond` to submit threshold signatures and `vote_pk` / `vote_reshared` to influence DKG and resharing outcomes. The TEE attestation requirement — the primary security boundary ensuring nodes run trusted, unmodified software — is bypassed for the window between revocation and the next lazy cleanup. This constitutes an **attestation authorization bypass** enabling a compromised or revoked node to contribute signing shares and governance votes as if it were still trusted.

This maps to the allowed impact: **High — participant/attestation authorization bypass that causes invalid bridge execution or double-spend conditions**, because a revoked node can produce signing shares for `verify_foreign_transaction` responses that the contract accepts.

### Likelihood Explanation

- TEE attestation certificates carry explicit expiry timestamps (demonstrated in the test suite with `expiry_timestamp_seconds`). [10](#0-9) 
- Docker image hashes are removed from the allowed set via normal governance (`vote_code_hash` whitelists a new hash and implicitly supersedes the old one after the upgrade deadline). [11](#0-10) 
- The lazy cleanup (`clean_invalid_attestations`) is bounded to 100 entries per resharing and is not called on every block. The window of exposure is therefore the entire inter-resharing period, which can be days to weeks.
- No privileged access is required: any existing participant whose attestation has since expired or been revoked can exploit this autonomously.

### Recommendation

`is_caller_an_attested_participant` should call `reverify_participants` after confirming the attestation exists, and return an error if the result is `TeeQuoteStatus::Invalid`. The `tee_upgrade_deadline_duration` needed by `reverify_participants` is available from `self.config` at the call sites in `lib.rs`. This mirrors the Aloe fix of replacing `borrowBalanceStored` with `borrowBalance` — replacing the cached stored value with a live re-evaluation.

### Proof of Concept

1. Node A calls `submit_participant_info` with a valid attestation tied to docker image hash `H` and expiry `T+86400`. The attestation is stored in `stored_attestations`.
2. Governance votes to remove docker image hash `H` from the allowed set (e.g., a vulnerability is found). `whitelist_tee_proposal` is called with a new hash `H'`.
3. Node A's attestation is now invalid under `reverify_participants` (its docker image hash `H` is no longer in `allowed_docker_image_hashes`).
4. Node A calls `respond(request, signature_response)`. The contract calls `assert_caller_is_attested_participant_and_protocol_active` → `is_caller_an_attested_participant`. This finds Node A's entry in `stored_attestations`, confirms the key match, and returns `Ok(())` — **without calling `reverify_participants`**.
5. The signature is accepted and the yield is resolved, producing a valid on-chain signature from a node running revoked software.

### Citations

**File:** crates/contract/src/tee/tee_state.rs (L150-203)
```rust
    /// Adds a participant attestation for the given node iff the attestation succeeds verification.
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

**File:** crates/contract/src/tee/tee_state.rs (L305-316)
```rust
    pub fn whitelist_tee_proposal(
        &mut self,
        tee_proposal: NodeImageHash,
        tee_upgrade_deadline_duration: Duration,
    ) {
        self.votes.clear_votes();
        // Add compose hashes for the new MPC image across all allowed launcher images
        self.allowed_launcher_images
            .add_mpc_image_compose_hashes(&tee_proposal);
        self.allowed_docker_image_hashes
            .insert(tee_proposal, tee_upgrade_deadline_duration);
    }
```

**File:** crates/contract/src/tee/tee_state.rs (L406-434)
```rust
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

**File:** crates/contract/src/tee/tee_state.rs (L631-690)
```rust
    fn clean_invalid_attestations__should_remove_expired_entries() {
        // Given: one fresh and one already-expired attestation stored.
        const FRESH_EXPIRY_SECONDS: u64 = 10_000;
        const STALE_EXPIRY_SECONDS: u64 = 1_000;
        const NOW_SECONDS: u64 = 5_000;

        testing_env!(VMContextBuilder::new().block_timestamp(0).build());

        let mut tee_state = TeeState::default();

        let fresh_node = NodeId {
            account_id: "fresh.near".parse().unwrap(),
            tls_public_key: bogus_ed25519_public_key(),
            account_public_key: bogus_ed25519_public_key(),
        };
        let stale_node = NodeId {
            account_id: "stale.near".parse().unwrap(),
            tls_public_key: bogus_ed25519_public_key(),
            account_public_key: bogus_ed25519_public_key(),
        };

        let fresh = Attestation::Mock(MockAttestation::WithConstraints {
            mpc_docker_image_hash: None,
            launcher_docker_compose_hash: None,
            expiry_timestamp_seconds: Some(FRESH_EXPIRY_SECONDS),
            expected_measurements: None,
        });
        let stale = Attestation::Mock(MockAttestation::WithConstraints {
            mpc_docker_image_hash: None,
            launcher_docker_compose_hash: None,
            expiry_timestamp_seconds: Some(STALE_EXPIRY_SECONDS),
            expected_measurements: None,
        });

        tee_state
            .add_participant(fresh_node.clone(), fresh, Duration::from_secs(0))
            .unwrap();
        tee_state
            .add_participant(stale_node.clone(), stale, Duration::from_secs(0))
            .unwrap();

        assert_eq!(tee_state.stored_attestations.len(), 2);

        // When: the clock advances past the stale entry's expiry and cleanup runs.
        set_block_timestamp(NOW_SECONDS * 1_000_000_000);
        let removed = tee_state.clean_invalid_attestations(Duration::from_secs(0), 100);

        // Then: only the expired entry is removed.
        assert_eq!(removed, 1);
        assert!(
            tee_state
                .stored_attestations
                .contains_key(&fresh_node.tls_public_key)
        );
        assert!(
            !tee_state
                .stored_attestations
                .contains_key(&stale_node.tls_public_key)
        );
    }
```

**File:** crates/contract/src/lib.rs (L563-573)
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
```

**File:** crates/contract/src/lib.rs (L1078-1082)
```rust
    pub fn start_keygen_instance(&mut self, key_event_id: KeyEventId) -> Result<(), Error> {
        log!("start_keygen_instance: signer={}", env::signer_account_id(),);

        self.assert_caller_is_attested_participant_and_protocol_active();

```

**File:** crates/contract/src/lib.rs (L1103-1115)
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
