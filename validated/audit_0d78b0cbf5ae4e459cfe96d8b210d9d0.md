### Title
Expired TEE Attestation Bypasses Participant Authorization in `respond()` and Key-Event Votes - (File: `crates/contract/src/tee/tee_state.rs`)

### Summary

`is_caller_an_attested_participant` verifies that a stored attestation **exists** and that identity fields match, but never checks whether the attestation is still **valid** (not expired, image hash still whitelisted). This is the direct analog of the external report: a check that should enforce two conditions — "is a participant" AND "has a currently-valid attestation" — only enforces one, allowing a node whose TEE attestation has lapsed to keep calling `respond()`, `vote_pk()`, `vote_reshared()`, and other privileged entry points.

---

### Finding Description

`is_caller_an_attested_participant` in `crates/contract/src/tee/tee_state.rs` is the sole attestation gate used by `assert_caller_is_attested_participant_and_protocol_active`, which guards every critical node-facing method:

- `respond()`, `respond_ckd()`, `respond_verify_foreign_tx()` — signature/CKD submission
- `vote_pk()`, `vote_reshared()`, `vote_abort_key_event_instance()` — key-event votes
- `start_keygen_instance()`, `start_reshare_instance()` — leader-only operations

The function performs four checks:

```rust
// crates/contract/src/tee/tee_state.rs  lines 469-498
pub(crate) fn is_caller_an_attested_participant(
    &self,
    participants: &Participants,
) -> Result<(), AttestationCheckError> {
    let signer_account_pk = env::signer_account_pk();
    let signer_id = env::signer_account_id();

    let info = participants
        .info(&signer_id)
        .ok_or(AttestationCheckError::CallerNotParticipant)?;   // ① in participants list?

    let attestation = self
        .stored_attestations
        .get(&info.tls_public_key)
        .ok_or(AttestationCheckError::AttestationNotFound)?;    // ② attestation stored?

    if attestation.node_id.account_id != signer_id {
        return Err(AttestationCheckError::AttestationOwnerMismatch); // ③ account_id matches?
    }

    let signer_ed25519 = Ed25519PublicKey::try_from(&signer_account_pk)
        .map_err(|_| AttestationCheckError::AttestationKeyMismatch)?;
    if attestation.node_id.account_public_key != signer_ed25519 {
        return Err(AttestationCheckError::AttestationKeyMismatch); // ④ account_pk matches?
    }

    Ok(())   // ← NEVER calls reverify_participants; attestation validity is not checked
}
``` [1](#0-0) 

The contract already has a function that performs the full validity check — `reverify_participants` — which re-runs expiry, image-hash, launcher-hash, and measurement checks against the stored `VerifiedAttestation`:

```rust
// crates/contract/src/tee/tee_state.rs  lines 206-232
pub(crate) fn reverify_participants(
    &self,
    node_id: &NodeId,
    tee_upgrade_deadline_duration: Duration,
) -> TeeQuoteStatus {
    ...
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
``` [2](#0-1) 

`reverify_participants` is called by `reverify_and_cleanup_participants` (used in `verify_tee()` and `vote_new_parameters()`) and by `clean_invalid_attestations`, but **never** by `is_caller_an_attested_participant`.

The parallel to the external report is exact:

| External report | NEAR MPC analog |
|---|---|
| `isBLSPublicKeyPartOfLSDNetwork` — checks "in network" only | `is_caller_an_attested_participant` — checks "stored attestation exists" only |
| `isBLSPublicKeyBanned` — checks "in network AND not banned" | `reverify_participants` — checks "attestation exists AND still valid" |
| Fix: use `isBLSPublicKeyBanned` | Fix: also call `reverify_participants` inside `is_caller_an_attested_participant` |

---

### Impact Explanation

**Medium — participant-state and contract execution-flow invariant broken.**

The TEE model's core invariant is: *only nodes running inside a valid, whitelisted TEE image may submit signatures or cast key-event votes*. Once a node's attestation expires (or its image hash is removed from the whitelist), it should be treated as untrusted. However, because `is_caller_an_attested_participant` never calls `reverify_participants`, the contract continues to accept `respond()` calls and key-event votes from that node until:

1. `verify_tee()` is called **and** the subsequent resharing fully completes (removing the node from the participant list), **or**
2. `clean_invalid_attestations` is called and removes the stale entry from `stored_attestations`.

During this window the node — which may no longer be running inside a trusted TEE — can submit threshold-signature responses that the contract will accept as authoritative, and can cast `vote_pk` / `vote_reshared` ballots that count toward key-event quorum. This breaks the production safety invariant that TEE validity is a continuous requirement, not a one-time admission check. [3](#0-2) [4](#0-3) 

---

### Likelihood Explanation

**Medium.**

TEE attestations carry an expiry timestamp enforced at submission time (`add_participant` calls `verify_locally` with the current block timestamp). In production, attestations expire on a schedule tied to certificate chains and the `tee_upgrade_deadline_duration` config. The gap between expiry and the next `verify_tee()` call (which nodes are expected to invoke periodically, roughly every 7 days per the design doc) is a predictable, recurring window. Any participant whose attestation lapses — whether through operator neglect, a certificate rotation delay, or a deliberate image-hash removal vote — automatically falls into the vulnerable state without any attacker action required. [5](#0-4) 

---

### Recommendation

Inside `is_caller_an_attested_participant`, after confirming the attestation exists and the identity fields match, add a call to `reverify_participants` and return an error if the result is not `TeeQuoteStatus::Valid`:

```rust
// After the existing identity checks:
let node_id = &attestation.node_id;
let tee_upgrade_deadline_duration = /* pass from config or as parameter */;
if !matches!(
    self.reverify_participants(node_id, tee_upgrade_deadline_duration),
    TeeQuoteStatus::Valid
) {
    return Err(AttestationCheckError::AttestationExpiredOrInvalid);
}
```

This mirrors the pattern already used in `reverify_and_cleanup_participants` and `conclude_node_migration`, and closes the gap between "attestation was once valid" and "attestation is currently valid". [6](#0-5) [7](#0-6) 

---

### Proof of Concept

1. Network is Running with participants P1, P2, P3 (threshold 2). All have valid attestations.
2. P3's attestation expires (e.g., certificate chain lapses). `verify_tee()` has not yet been called.
3. A user submits a `sign()` request. The off-chain MPC protocol runs; P3 participates because the off-chain layer has no on-chain expiry check.
4. P3 calls `respond(request, signature)` on-chain.
5. `assert_caller_is_attested_participant_and_protocol_active()` → `is_caller_an_attested_participant()`:
   - P3 is still in the participants list ✓
   - P3's entry is still in `stored_attestations` ✓
   - `account_id` and `account_public_key` match ✓
   - **`reverify_participants` is never called** — expiry is not checked ✓ (bug)
6. The contract accepts P3's response and resolves the yield, delivering the signature to the user.
7. P3 has successfully submitted a threshold signature despite its TEE attestation being expired. [1](#0-0) [8](#0-7)

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

**File:** crates/contract/src/tee/tee_state.rs (L246-263)
```rust
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

**File:** crates/contract/src/lib.rs (L2593-2604)
```rust
        if !(matches!(
            self.tee_state.reverify_participants(
                &node_id,
                Duration::from_secs(self.config.tee_upgrade_deadline_duration_seconds),
            ),
            TeeQuoteStatus::Valid
        )) {
            return Err(errors::InvalidParameters::InvalidTeeRemoteAttestation {
                reason: "destination node TEE quote is invalid".into(),
            }
            .into());
        };
```
