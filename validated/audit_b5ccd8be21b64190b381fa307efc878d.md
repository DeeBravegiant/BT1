### Title
Dev TEE Measurements Statically Compiled Into Production Binary Allow Dev-Firmware Node to Pass Attestation — (`crates/mpc-attestation/src/attestation.rs`)

### Summary

`default_measurements()` unconditionally compiles both `assets/tcb_info.json` (production) and `assets/tcb_info_dev.json` (dev) into every build. The contract's migration seeds the on-chain `AllowedMeasurements` from this function. As a result, a node running dev firmware (different MRTD/RTMR0-2 from production) but the same MPC Docker image can pass all three attestation guards and continue participating in threshold signing.

### Finding Description

`default_measurements()` in `crates/mpc-attestation/src/attestation.rs` returns a static array of two `ExpectedMeasurements` entries — one from `assets/tcb_info.json` and one from `assets/tcb_info_dev.json` — with no compile-time feature gate: [1](#0-0) 

The contract's `AllowedMeasurements` struct documents that it is "seeded from `default_measurements()` on migration": [2](#0-1) 

`TeeState::add_participant` calls `get_accepted_measurements()`, which returns the on-chain `AllowedMeasurements` list (not a hardcoded set), and passes it to `verify_locally`: [3](#0-2) 

`get_accepted_measurements` returns whatever is stored on-chain: [4](#0-3) 

`verify_any_measurements` iterates the accepted list and returns `Ok` on the first match: [5](#0-4) 

Because the migration seeds both prod and dev `ExpectedMeasurements` into the on-chain list, a node whose MRTD/RTMR0-2 match only the dev set passes this check.

### Impact Explanation

A node operator who is already a participant can switch their node to dev firmware. On the next attestation refresh, `verify_any_measurements` matches the dev `ExpectedMeasurements`, the MPC image hash and launcher compose hash checks pass (same Docker image and compose as production), and the attestation is accepted. The node continues to participate in threshold signing with unaudited, non-production firmware, violating the invariant that only audited production firmware may participate.

### Likelihood Explanation

The TODO comment in the source explicitly acknowledges the risk: [6](#0-5) 

The preconditions are realistic: a participant node operator controls their own hardware and firmware, and the same MPC Docker image can run on dev firmware. No threshold collusion is required — a single participant can exploit this unilaterally.

### Recommendation

1. Remove `assets/tcb_info_dev.json` from `default_measurements()` in production builds (resolve TODO #1433). Gate dev measurements behind a non-default Cargo feature flag.
2. After migration, provide an explicit governance action to remove dev measurements from the on-chain `AllowedMeasurements` if they were seeded.
3. Add a compile-time assertion or CI check that verifies the production binary does not embed dev measurement values.

### Proof of Concept

Build the production binary (default features). Construct a `DstackAttestation` whose `TcbInfo` contains MRTD/RTMR0-2 values matching `assets/tcb_info_dev.json` and a valid MPC image hash already in the on-chain allowlist. Call `add_participant` on the contract. The attestation passes `verify_any_measurements` because the dev `ExpectedMeasurements` entry is present in the seeded `AllowedMeasurements`, and the node is accepted as a valid participant.

### Citations

**File:** crates/mpc-attestation/src/attestation.rs (L259-266)
```rust
pub fn default_measurements() -> &'static [ExpectedMeasurements] {
    static MEASUREMENTS: [ExpectedMeasurements; 2] = [
        include_measurements!("assets/tcb_info.json"),
        // TODO(#1433): Security - remove dev measurements from production builds after testing is complete
        include_measurements!("assets/tcb_info_dev.json"),
    ];
    &MEASUREMENTS
}
```

**File:** crates/contract/src/tee/measurements.rs (L97-108)
```rust
/// Collection of allowed OS measurements. Managed via voting (add requires threshold,
/// remove requires unanimity). Starts empty on fresh contracts (consistent with docker
/// image hashes and launcher hashes); seeded from `default_measurements()` on migration.
/// Once populated, at least one measurement must remain.
#[derive(Clone, Default, Debug, PartialEq, Eq, BorshSerialize, BorshDeserialize)]
#[cfg_attr(
    all(feature = "abi", not(target_arch = "wasm32")),
    derive(borsh::BorshSchema)
)]
pub(crate) struct AllowedMeasurements {
    entries: Vec<ContractExpectedMeasurements>,
}
```

**File:** crates/contract/src/tee/tee_state.rs (L163-175)
```rust
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
```

**File:** crates/contract/src/tee/tee_state.rs (L389-391)
```rust
    fn get_accepted_measurements(&self) -> Vec<mpc_attestation::attestation::ExpectedMeasurements> {
        self.allowed_measurements.to_attestation_measurements()
    }
```

**File:** crates/attestation/src/attestation.rs (L286-309)
```rust
    fn verify_any_measurements(
        &self,
        report_data: &TDReport10,
        tcb_info: &TcbInfo,
        accepted_measurements: &[ExpectedMeasurements],
    ) -> Result<ExpectedMeasurements, VerificationError> {
        for expected in accepted_measurements {
            if self
                .verify_static_rtmrs(report_data, tcb_info, expected)
                .is_ok()
                && self
                    .verify_key_provider_digest(tcb_info, &expected.key_provider_event_digest)
                    .is_ok()
            {
                return Ok(*expected); // found a valid match
            }
        }

        Err(VerificationError::WrongHash {
            name: "expected_measurements",
            expected: "one of the embedded TCB info sets (prod or dev)".into(),
            found: "none matched".into(),
        })
    }
```
