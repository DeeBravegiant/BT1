### Title
`MockAttestation::Valid` Permanently Bypasses TEE Attestation Check in `submit_participant_info` — (`crates/mpc-attestation/src/attestation.rs`, `crates/contract/src/lib.rs`)

---

### Summary

The `MockAttestation::Valid` variant is hardcoded to unconditionally return `Ok(())` in both initial verification and re-verification paths. Because `submit_participant_info` accepts this variant from any caller without a production guard, any existing MPC participant can replace their legitimate `Dstack` attestation with `MockAttestation::Valid` at any time. The periodic `verify_tee` / `reverify_and_cleanup_participants` mechanism also passes unconditionally for this variant, so the bypass is permanent and undetectable by the contract's own safety sweep.

---

### Finding Description

**Root cause — always-passing variant:**

`MockAttestation::Valid` is defined as the `#[default]` variant of `MockAttestation` and its `verify_constraints` implementation unconditionally returns `Ok(())`: [1](#0-0) 

```rust
match self {
    MockAttestation::Valid => Ok(()),   // ← always passes, no state checked
    MockAttestation::Invalid => Err(VerificationError::InvalidMockAttestation),
```

This is structurally identical to the `marketWhitelist` issue: a "check" function that always returns the permissive result because the underlying state is never consulted.

**Root cause — no production guard in `submit_participant_info`:**

The public `submit_participant_info` endpoint accepts `dtos::Attestation`, which includes the `Mock(MockAttestation::Valid)` variant, and passes it directly to `tee_state.add_participant` with no check that rejects `Mock` attestations in production: [2](#0-1) 

There is no `cfg(test)` gate, feature flag, or runtime guard anywhere in this path. The design document explicitly acknowledges this: *"Attestation::Mock stays in this iteration … removing Mock is a separate cleanup, not in scope here."*

**Root cause — re-verification also always passes:**

When `verify_tee` runs its periodic sweep via `reverify_and_cleanup_participants`, it calls `re_verify` on every stored attestation. For a stored `VerifiedAttestation::Mock(MockAttestation::Valid)`, `re_verify` delegates back to `verify_constraints`, which again returns `Ok(())` unconditionally: [3](#0-2) 

The participant is therefore classified as `TeeQuoteStatus::Valid` and is never evicted.

**Initialization state:**

`TeeState` is initialized with all allowlists empty (`Default::default()`). A `Dstack` attestation submitted against an empty `allowed_docker_image_hashes` list is correctly rejected. However, `MockAttestation::Valid` bypasses every allowlist check entirely, making the initialized-empty state irrelevant for this variant: [4](#0-3) 

---

### Impact Explanation

An existing MPC participant (a Byzantine node strictly below the signing threshold) can:

1. Call `submit_participant_info(Attestation::Mock(MockAttestation::Valid), tls_pk)` at any time — including after their legitimate `Dstack` attestation expires.
2. Their stored attestation is silently replaced with the always-passing mock.
3. Every subsequent `verify_tee` sweep classifies them as `TeeQuoteStatus::Valid` and leaves them in the active participant set.
4. They continue to pass `assert_caller_is_attested_participant_and_protocol_active` (used in `respond_verify_foreign_tx` and governance calls) without running in a TEE.

The production safety invariant — *all active participants must hold a valid, hardware-rooted TEE attestation* — is permanently broken for that participant, and the contract's own enforcement mechanism (`verify_tee`) cannot detect or remediate it. This breaks the participant-state accounting invariant that the MPC security model depends on.

---

### Likelihood Explanation

- **Attacker-controlled entry path:** `submit_participant_info` is a public, payable endpoint callable by any NEAR account. No privileged role is required.
- **No threshold collusion needed:** A single existing participant can perform this unilaterally. They do not need to coordinate with other participants.
- **Persistent:** The bypass survives every `verify_tee` sweep because `re_verify` on `MockAttestation::Valid` always returns `Ok(())`.
- **No special tooling:** The `Mock` variant is part of the public DTO schema (`dtos::Attestation`), so any NEAR SDK client can construct and submit it.

---

### Recommendation

1. **Reject `Mock` attestations in production `submit_participant_info`:** Add a guard that returns an error if the submitted attestation is any `Mock` variant when the contract is not in a test/devnet mode. A compile-time feature flag (`#[cfg(feature = "mock-attestation")]`) is the cleanest approach.

2. **Reject `Mock` attestations in `re_verify`:** Even if step 1 is implemented, existing stored mock entries (from the initialization path) should be rejected by `re_verify` in production builds, so they age out on the next `verify_tee` sweep.

3. **Track the cleanup:** The design document already acknowledges this as a known gap. Issue [#1087](https://github.com/near/mpc/issues/903) and the `TODO(#1087)` comment in `with_mocked_participant_attestations` confirm the team is aware. This finding demonstrates that the gap is exploitable by a single participant, not just a theoretical concern.

---

### Proof of Concept

```rust
// Any existing participant calls this on mainnet:
contract.submit_participant_info(
    Attestation::Mock(MockAttestation::Valid),  // always passes
    their_tls_public_key,
);

// After this call:
// 1. stored_attestations[tls_pk] = VerifiedAttestation::Mock(MockAttestation::Valid)
// 2. verify_tee() → reverify_and_cleanup_participants() → re_verify() → Ok(())
//    → TeeQuoteStatus::Valid → participant stays in active set
// 3. assert_caller_is_attested_participant_and_protocol_active() → passes
// 4. Participant operates outside TEE indefinitely, undetected by the contract.
``` [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/mpc-attestation/src/attestation.rs (L141-143)
```rust
        match self {
            MockAttestation::Valid => Ok(()),
            MockAttestation::Invalid => Err(VerificationError::InvalidMockAttestation),
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

**File:** crates/contract/src/tee/tee_state.rs (L88-99)
```rust
impl Default for TeeState {
    fn default() -> Self {
        Self {
            allowed_docker_image_hashes: Default::default(),
            allowed_launcher_images: Default::default(),
            votes: Default::default(),
            launcher_votes: Default::default(),
            stored_attestations: IterableMap::new(StorageKey::StoredAttestations),
            allowed_measurements: Default::default(),
            measurement_votes: Default::default(),
        }
    }
```

**File:** crates/contract/src/tee/tee_state.rs (L246-275)
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
```
