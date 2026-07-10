### Title
Expired TEE Attestations Not Checked in `is_caller_an_attested_participant`, Allowing Expired Participants to Submit Signatures — (File: `crates/contract/src/tee/tee_state.rs`)

---

### Summary

`TeeState::is_caller_an_attested_participant` verifies that a caller is a registered participant with a stored attestation, but **never checks whether that attestation has expired**. This is the direct analog of the ENS NameWrapper bug: just as an expired ENS name was misclassified as "unwrapped" (not "unregistered"), an expired TEE attestation is misclassified as "valid" here. The result is that a participant whose TEE attestation has lapsed can continue to call `respond`, `respond_ckd`, `vote_reshared`, `vote_pk`, `start_keygen_instance`, and `start_reshare_instance` — all of which are gated exclusively by this check — bypassing the TEE attestation validity requirement.

---

### Finding Description

`is_caller_an_attested_participant` (lines 469–498 of `tee_state.rs`) performs four checks:

1. Is the caller in the `Participants` list?
2. Is there a `NodeAttestation` stored under their TLS key?
3. Does `attestation.node_id.account_id` match the signer?
4. Does `attestation.node_id.account_public_key` match the signer's Ed25519 key? [1](#0-0) 

It does **not** call `re_verify` on the stored `VerifiedAttestation` and does not inspect the attestation's expiry timestamp. The expiry check exists in `reverify_participants` / `reverify_and_cleanup_participants`, but those are only invoked from `verify_tee()` and `vote_new_parameters()`. [2](#0-1) 

The `respond` function calls `assert_caller_is_attested_participant_and_protocol_active()`, which in turn calls `is_caller_an_attested_participant`. Because expiry is never checked there, a participant whose attestation has lapsed passes the gate: [3](#0-2) [4](#0-3) 

The same gate protects `respond_ckd`, `vote_reshared`, `vote_pk`, `start_keygen_instance`, and `start_reshare_instance`.

The `accept_requests` flag is a coarser guard: it is only set to `false` when `verify_tee()` determines that **fewer than `threshold`** participants have valid attestations. If only one participant's attestation expires while the rest remain valid, `accept_requests` stays `true` and the expired participant is not blocked at the `respond` level. [5](#0-4) 

The ENS parallel is exact:

| ENS NameWrapper | NEAR MPC |
|---|---|
| Expired name → treated as "unwrapped" not "unregistered" | Expired attestation → treated as "valid" not "expired" |
| Only `wrappedOwner == 0` checked, not `ens.owner == 0` too | Only identity fields checked, not `re_verify()` |
| Bypass of `CANNOT_CREATE_SUBDOMAIN` fuse | Bypass of TEE attestation requirement for signing |

---

### Impact Explanation

A participant whose TEE attestation has expired — meaning their TEE environment is no longer certified as trustworthy — can continue to:

- Submit threshold signatures via `respond` (unauthorized signing by an untrusted node)
- Submit CKD responses via `respond_ckd`
- Cast decisive votes in `vote_reshared`, advancing or completing a resharing without a valid TEE guarantee

The production safety invariant — that only nodes running in a verified TEE can participate in signing — is broken. A node that has left the TEE (e.g., migrated to a non-TEE environment after its certificate expired) retains full signing authority until `verify_tee()` is explicitly called and resharing completes. During that window, signatures produced by the expired node are accepted by the contract as if they came from a trusted TEE participant.

This maps to the allowed Medium impact: **participant-state manipulation that breaks production safety/accounting invariants**.

---

### Likelihood Explanation

TEE attestations carry explicit expiry timestamps and expire on a regular schedule (days to months). `verify_tee()` is not called automatically on every block; it must be triggered explicitly by a participant. The gap between attestation expiry and the next `verify_tee()` call — plus the time for resharing to complete — can span many blocks. During this entire window the expired participant passes `is_caller_an_attested_participant` without any expiry check. Any participant whose attestation lapses (hardware refresh, certificate rotation, software update delay) triggers this condition without any attacker action required.

---

### Recommendation

Add an expiry re-verification step inside `is_caller_an_attested_participant`. After retrieving the stored `NodeAttestation`, call `re_verify` on its `VerifiedAttestation` using the current block timestamp and the current allowed-hash lists. Return `Err(AttestationCheckError::AttestationExpired)` (a new variant) if re-verification fails. This mirrors the fix applied to the ENS bug: checking **both** conditions (identity **and** current validity) rather than identity alone.

```rust
// After the existing identity checks:
let tee_upgrade_deadline = /* read from config or pass as parameter */;
let now_s = env::block_timestamp() / 1_000_000_000;
attestation
    .verified_attestation
    .re_verify(now_s, &allowed_mpc_hashes, &allowed_launcher_hashes, &measurements)
    .map_err(|_| AttestationCheckError::AttestationExpired)?;
```

---

### Proof of Concept

1. Contract is `Running` with participants `[A, B, C]`, threshold 2. All attestations are initially valid.
2. Participant A's attestation reaches its `expiry_timestamp_seconds`. No one calls `verify_tee()` yet.
3. A user submits a `sign()` request. `accept_requests` is still `true` (B and C are valid, threshold is met).
4. Participant A calls `respond(request, signature)`.
5. `assert_caller_is_attested_participant_and_protocol_active()` → `is_caller_an_attested_participant()` → finds A in participants list, finds A's `NodeAttestation` in `stored_attestations`, identity fields match → returns `Ok(())`. **Expiry is never checked.**
6. The contract accepts A's signature response and resolves the pending yield — a signature produced by a node that is no longer TEE-certified is delivered to the user.
7. This continues until `verify_tee()` is called and resharing completes, which may take an arbitrarily long time. [1](#0-0) [6](#0-5) [7](#0-6)

### Citations

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

**File:** crates/contract/src/lib.rs (L1693-1743)
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
