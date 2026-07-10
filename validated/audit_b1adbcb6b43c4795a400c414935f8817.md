### Title
`MockAttestation::Valid` Stub Bypasses TEE Verification in Production Attestation Path — (`File: crates/mpc-attestation/src/attestation.rs`)

### Summary

The production `Attestation` enum exposes a `Mock(MockAttestation::Valid)` variant that unconditionally returns `Ok(())` from `verify_locally` without invoking any DCAP quote verification. Because this variant is part of the public wire type accepted by `submit_participant_info`, an unprivileged caller can submit it to register as a TEE-verified participant without running inside a Trusted Execution Environment, bypassing the hardware attestation gate that protects threshold-signing participation.

### Finding Description

In `crates/mpc-attestation/src/attestation.rs`, the `Attestation` enum is the public type submitted to the contract's `submit_participant_info` method:

```rust
pub enum Attestation {
    Dstack(DstackAttestation),
    Mock(MockAttestation),
}
```

The `MockAttestation::Valid` variant's `verify_constraints` implementation is a direct stub — it performs no cryptographic work and returns `Ok(())` unconditionally:

```rust
MockAttestation::Valid => Ok(()),
```

The `verify_locally` dispatch in `Attestation` routes `Mock` variants through this same stub path:

```rust
Self::Mock(mock_attestation) => mock_attestation.verify(
    current_timestamp_seconds,
    allowed_mpc_docker_image_hashes,
    allowed_launcher_docker_compose_hashes,
    accepted_measurements,
),
```

`MockAttestation::verify` calls `verify_constraints`, which for `Valid` immediately returns `Ok(AcceptedAttestation::mock(self))` — no DCAP quote, no Intel collateral check, no report-data binding, no image-hash check.

In `crates/contract/src/tee/tee_state.rs`, `add_participant` calls `attestation.verify_locally(...)` and stores the result as a `VerifiedAttestation::Mock(MockAttestation::Valid)` entry in `stored_attestations`. The `re_verify` path for `Mock` also routes through `verify_constraints`, so the stored entry continues to pass all subsequent re-verification sweeps (`reverify_participants`, `clean_invalid_attestations`) indefinitely.

The `with_mocked_participant_attestations` function confirms this path is used in production initialization:

```rust
verified_attestation: VerifiedAttestation::Mock(
    attestation::MockAttestation::Valid,
),
```

Because `Attestation` is a `#[derive(Serialize, Deserialize, BorshSerialize, BorshDeserialize)]` public enum with no production guard on the `Mock` arm, any caller who can reach `submit_participant_info` can construct and submit `Attestation::Mock(MockAttestation::Valid)` over the wire.

### Impact Explanation

An attacker who submits `Attestation::Mock(MockAttestation::Valid)` to `submit_participant_info` is stored in `stored_attestations` as a fully verified TEE participant. The contract's `is_caller_an_attested_participant` check — which gates threshold-signing operations — will pass for this attacker. The attacker can then participate in DKG, resharing, and signing ceremonies without running inside a TDX CVM, effectively bypassing the hardware TEE requirement that is the primary security boundary for key-share confidentiality and unauthorized signing prevention.

**Impact class**: Bypass of threshold-signature requirements / unauthorized access to MPC signing capability — Critical.

### Likelihood Explanation

The `Attestation` type is a public, serializable enum. Constructing `Attestation::Mock(MockAttestation::Valid)` requires no privileged access, no leaked keys, and no TEE hardware — only the ability to call `submit_participant_info` with a crafted payload. Any NEAR account can do this.

### Recommendation

- **Short term**: Add an explicit production guard in `add_participant` (or at the `submit_participant_info` call site) that rejects `Attestation::Mock(_)` variants unless a compile-time `dev-utils` / `sandbox-test-methods` feature flag is active. This mirrors the pattern already used for `sandbox_test_methods`.
- **Long term**: Move `MockAttestation` out of the production `Attestation` enum entirely into a test-only type gated by `#[cfg(test)]` or a feature flag, so the stub path cannot be reached in a production WASM build.

### Proof of Concept

1. Attacker constructs the Borsh-serialized form of `Attestation::Mock(MockAttestation::Valid)`.
2. Attacker calls `submit_participant_info(node_id, attestation)` on the deployed `mpc-contract` with this payload.
3. `add_participant` calls `attestation.verify_locally(...)` → dispatches to `MockAttestation::Valid` → `verify_constraints` returns `Ok(())` immediately.
4. The attacker's `NodeId` is inserted into `stored_attestations` as `VerifiedAttestation::Mock(MockAttestation::Valid)`.
5. All subsequent `re_verify` calls on this entry also return `Ok(())` (same stub path), so the entry survives `clean_invalid_attestations` sweeps indefinitely.
6. `is_caller_an_attested_participant` now returns `Ok(())` for the attacker, granting full participation rights in threshold-signing ceremonies without any TEE hardware.

---

**Root cause files:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/mpc-attestation/src/attestation.rs (L30-35)
```rust
#[expect(clippy::large_enum_variant)]
#[derive(Clone, Debug, Serialize, Deserialize, BorshSerialize, BorshDeserialize)]
pub enum Attestation {
    Dstack(DstackAttestation),
    Mock(MockAttestation),
}
```

**File:** crates/mpc-attestation/src/attestation.rs (L141-143)
```rust
        match self {
            MockAttestation::Valid => Ok(()),
            MockAttestation::Invalid => Err(VerificationError::InvalidMockAttestation),
```

**File:** crates/mpc-attestation/src/attestation.rs (L373-380)
```rust
            }
            Self::Mock(mock_attestation) => mock_attestation.verify(
                current_timestamp_seconds,
                allowed_mpc_docker_image_hashes,
                allowed_launcher_docker_compose_hashes,
                accepted_measurements,
            ),
        }
```

**File:** crates/contract/src/tee/tee_state.rs (L103-143)
```rust
    /// Creates a [`TeeState`] with an initial set of participants that will receive a valid mocked attestation.
    pub(crate) fn with_mocked_participant_attestations(participants: &Participants) -> Self {
        let mut tee_state = Self::default();

        for (account_id, _, participant_info) in participants.participants() {
            let tls_public_key = participant_info.tls_public_key.clone();
            // TODO(#1087): replace account_public_key with a real account public
            // key passed in by the caller. `Participants` does not currently
            // carry the operator's account public key, so a mocked entry
            // cannot record the real one and we use the TLS key as a unique
            // per-participant placeholder. The mock keeps the
            // participant from being kicked out of an empty `TeeState` until
            // a real `submit_participant_info` call replaces it (keyed by
            // TLS), but any caller-facing check that compares
            // `signer_account_pk` against the stored key will fail until
            // then. #1087 tracks threading real attestations through
            // initialization so this sentinel can go away.
            let node_id = NodeId {
                account_id: account_id.clone(),
                tls_public_key: tls_public_key.clone(),
                // Use tls_public_key as account_public_key instead of hardcoded
                // Ed25519PublicKey::from([0u8; 32]) so that same account public
                // key isn't associated with different tls keys.
                // This is not a fix for above issue: #1087, which should be
                // addressed outside this PR.
                account_public_key: tls_public_key.clone(),
            };

            tee_state.stored_attestations.insert(
                tls_public_key,
                NodeAttestation {
                    node_id,
                    verified_attestation: VerifiedAttestation::Mock(
                        attestation::MockAttestation::Valid,
                    ),
                },
            );
        }

        tee_state
    }
```

**File:** crates/contract/src/tee/tee_state.rs (L165-176)
```rust
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

```
