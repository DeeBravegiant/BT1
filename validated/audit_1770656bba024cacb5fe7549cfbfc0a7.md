### Title
`MockAttestation::Valid` Bypasses TEE Hardware Verification in Production Contract — (File: `crates/mpc-attestation/src/attestation.rs`)

---

### Summary

The production `mpc-contract` accepts `Attestation::Mock(MockAttestation::Valid)` from any caller via `submit_participant_info` with no environment guard or restriction. This variant unconditionally passes every verification gate — initial attestation, periodic re-verification, and the `is_caller_an_attested_participant` check that gates threshold-critical operations — without any Intel TDX hardware validation. The result is a direct analog to the external report: just as a malicious Gnosis Safe singleton can satisfy a codehash check while faking ownership, a `MockAttestation::Valid` satisfies every TEE attestation check while proving nothing about the underlying hardware or software.

---

### Finding Description

**Root cause — `MockAttestation::Valid` always returns `Ok(())`:**

`MockAttestation::verify_constraints` is the single function that all verification paths call. For the `Valid` variant it is a no-op:

```rust
// crates/mpc-attestation/src/attestation.rs:141-142
match self {
    MockAttestation::Valid => Ok(()),   // ← no image hash, no expiry, no measurements
```

This means:
- `add_participant` (called by `submit_participant_info`) accepts it unconditionally.
- `re_verify` (called by `verify_tee` and `clean_invalid_attestations`) accepts it unconditionally and forever — `MockAttestation::Valid` carries no `expiry_timestamp_seconds`, so it never ages out.
- `is_caller_an_attested_participant` (called before every threshold-critical operation) does not inspect the `VerifiedAttestation` variant at all; it only checks that an entry exists and that the stored `account_id` / `account_public_key` match the signer.

**Production contract accepts `Mock` with no guard:**

`submit_participant_info` in `crates/contract/src/lib.rs` (lines 760–851) accepts any `dtos::Attestation` variant, converts it, and passes it to `tee_state.add_participant`. There is no `#[cfg]` gate, no check for whether the allowed image-hash list is non-empty, and no rejection of the `Mock` variant. The public ABI snapshot (`crates/contract/tests/snapshots/abi__abi_has_not_changed.snap`) exposes `MockAttestation` as a first-class schema type.

The operator guide itself confirms this is reachable on the live network:

> `{ "Mock": "Valid" }` — a mock attestation. Acceptable on testnet during the transition phase, but means the node is **not** running in a TEE. Many existing testnet entries are in this state.

**`is_caller_an_attested_participant` does not distinguish `Dstack` from `Mock`:**

```rust
// crates/contract/src/tee/tee_state.rs:469-498
pub(crate) fn is_caller_an_attested_participant(
    &self,
    participants: &Participants,
) -> Result<(), AttestationCheckError> {
    // ...
    let attestation = self.stored_attestations.get(&info.tls_public_key)
        .ok_or(AttestationCheckError::AttestationNotFound)?;
    // checks account_id and account_public_key only — never inspects VerifiedAttestation variant
    Ok(())
}
```

This function gates `vote_pk`, `vote_reshared`, `respond`, `respond_ckd`, and `start_keygen_instance` / `start_reshare_instance`. A participant whose stored entry is `VerifiedAttestation::Mock(MockAttestation::Valid)` passes identically to one with `VerifiedAttestation::Dstack(...)`.

**`verify_tee` / `clean_invalid_attestations` never evict a `Mock::Valid` entry:**

`reverify_participants` → `re_verify` → `MockAttestation::verify_constraints` → `Ok(())`. The entry is permanent and invisible to all cleanup sweeps.

**Attack path (no extra privilege beyond participant status):**

1. A prospective participant (or an existing one) calls `submit_participant_info(Attestation::Mock(MockAttestation::Valid), tls_public_key)`.
2. The contract stores `VerifiedAttestation::Mock(MockAttestation::Valid)` — passes all checks.
3. Existing participants vote the account into the active set via `vote_new_parameters`. The vote is over `(account_id, url, tls_public_key)` — the attestation type is not part of the proposal and is not visible to voters on-chain.
4. The participant now passes `is_caller_an_attested_participant` and can call `respond`, `vote_pk`, `vote_reshared`, etc.
5. `verify_tee` never evicts them; `clean_invalid_attestations` never removes them.
6. The participant operates indefinitely without genuine TEE protection.

---

### Impact Explanation

The TEE requirement exists to ensure that MPC key shares are generated and held inside hardware-isolated enclaves, so that no single node operator can extract a share. A participant who bypasses this requirement via `MockAttestation::Valid`:

- Receives a key share during DKG without hardware isolation — the share is extractable from process memory.
- Participates in threshold signing and resharing as a fully trusted node.
- Is never evicted by `verify_tee`, so the contract's safety invariant ("all active participants run in genuine TEEs") is silently broken.

If the bypassing participant is malicious, they hold a live key share outside any TEE. Combined with shares from other compromised nodes (below the signing threshold), this materially enables key-share recovery and unauthorized signature issuance. Even without collusion, the invariant break is a production safety violation: the contract reports all participants as TEE-verified when they are not.

**Allowed impact matched:** Bypass of threshold-signature requirements / unauthorized access to MPC key shares or signing capability that materially enables forgery or secret recovery; and participant-state / contract execution-flow manipulation that breaks production safety invariants.

---

### Likelihood Explanation

- `MockAttestation::Valid` is part of the public, stable ABI — any NEAR account can construct and submit it.
- No environment flag, no image-hash-list check, and no `Mock`-rejection guard exists in the production contract.
- Existing participants vote on `(account_id, tls_public_key)` — they cannot distinguish a mock-attested candidate from a real one on-chain.
- The mock entry never expires, so a one-time submission grants permanent "attested" status.
- The operator guide explicitly documents that mock attestations appear on the live testnet network today.

Likelihood: **High** — the entry point is the public `submit_participant_info` method, the payload is trivially constructable, and no on-chain mechanism prevents it.

---

### Recommendation

1. **Compile-time guard (preferred):** Wrap the `Mock` branch of `submit_participant_info` in a `#[cfg(feature = "mock-attestation")]` feature that is excluded from production WASM builds. The `tee-verifier` design already plans to remove `Attestation::Mock` once the stub supersedes it.

2. **Runtime guard (interim):** Reject `Attestation::Mock` when the allowed image-hash list is non-empty (i.e., when the contract is operating in TEE-enforced mode):

```rust
if matches!(proposed_participant_attestation, Attestation::Mock(_))
    && !self.tee_state.get_allowed_mpc_docker_image_hashes(...).is_empty()
{
    return Err(InvalidParameters::InvalidTeeRemoteAttestation {
        reason: "Mock attestations are not accepted in TEE-enforced mode".into(),
    }.into());
}
```

3. **`is_caller_an_attested_participant` hardening:** Add a check that the stored `VerifiedAttestation` is `Dstack` (not `Mock`) when the contract is in TEE-enforced mode, so that even a previously stored mock entry cannot pass the gate after TEE enforcement is activated.

---

### Proof of Concept

```rust
// Any participant account can call this on the live contract:
contract.submit_participant_info(
    Attestation::Mock(MockAttestation::Valid),  // no TDX quote, no collateral, no measurements
    tls_public_key,
);

// After being voted into the participant set, the account passes:
contract.assert_caller_is_attested_participant_and_protocol_active();
// → Ok(()) — indistinguishable from a genuine Dstack-attested participant

// verify_tee() never evicts the entry:
contract.verify_tee();
// → TeeValidationResult::Full — mock always passes re_verify

// The participant can now call respond(), vote_pk(), vote_reshared(), etc.
// without running in any TEE, holding an unprotected key share.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/mpc-attestation/src/attestation.rs (L24-28)
```rust
/// How long an accepted attestation stays trusted before it must be
/// re-verified via [`VerifiedAttestation::re_verify`]. Nodes resubmit hourly,
/// well within this window, so valid attestations refresh in time.
// TODO(#1639): extract timestamp from certificate itself
pub const DEFAULT_EXPIRATION_DURATION_SECONDS: u64 = 60 * 60 * 24; // 1 day
```

**File:** crates/mpc-attestation/src/attestation.rs (L141-142)
```rust
        match self {
            MockAttestation::Valid => Ok(()),
```

**File:** crates/mpc-attestation/src/attestation.rs (L248-253)
```rust
            Self::Mock(mock_attestation) => mock_attestation.verify_constraints(
                timestamp_seconds,
                allowed_mpc_docker_image_hashes,
                allowed_launcher_docker_compose_hashes,
                allowed_measurements,
            ),
```

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

**File:** crates/contract/src/lib.rs (L760-815)
```rust
    pub fn submit_participant_info(
        &mut self,
        proposed_participant_attestation: dtos::Attestation,
        tls_public_key: dtos::Ed25519PublicKey,
    ) -> Result<(), Error> {
        let proposed_participant_attestation =
            proposed_participant_attestation.try_into_contract_type()?;

        let account_key = env::signer_account_pk();
        let account_id = Self::assert_caller_is_signer();

        log!(
            "submit_participant_info: signer={}, proposed_participant_attestation={:?}, account_key={:?}",
            account_id,
            proposed_participant_attestation,
            account_key
        );

        // Save the initial storage usage to know how much to charge the proposer for the storage
        // used
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
