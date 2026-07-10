### Title
Node-Side TEE Attestation Validation Result Silently Discarded Before On-Chain Submission - (File: crates/node/src/tee/remote_attestation.rs)

### Summary

`validate_and_submit_remote_attestation` performs a local pre-flight check on the node's own TDX attestation quote, then unconditionally discards the result and submits the attestation to the on-chain contract regardless of whether validation passed or failed. This is structurally identical to the M-06 Oracle pattern: a data-quality check is executed but its outcome is never enforced.

### Finding Description

In `crates/node/src/tee/remote_attestation.rs`, the public entry point for attestation submission is:

```rust
pub async fn validate_and_submit_remote_attestation(
    tx_sender: impl TransactionSender,
    attestation: Attestation,
    tls_public_key: Ed25519PublicKey,
    account_public_key: Ed25519PublicKey,
    allowed_docker_image_hashes: &[NodeImageHash],
    allowed_launcher_compose_hashes: &[LauncherDockerComposeHash],
) -> anyhow::Result<()> {
    let _ = validate_remote_attestation(          // result thrown away
        &attestation,
        tls_public_key.clone(),
        account_public_key,
        allowed_docker_image_hashes,
        allowed_launcher_compose_hashes,
    )
    .inspect_err(|err| {
        // We could also return here, but for the moment I am just logging the
        // attestation failure error and letting the submission continue
        tracing::warn!("Attestation is not valid: {err}");
    });
    submit_remote_attestation(tx_sender, attestation, tls_public_key).await
}
``` [1](#0-0) 

The inner helper `validate_remote_attestation` performs a full local DCAP verification — binding the TLS key and account key into `ReportDataV1`, checking the current wall-clock timestamp, verifying the image hash and launcher compose hash, and checking `default_measurements()`: [2](#0-1) 

The `let _ = ...` idiom and the inline comment ("for the moment I am just logging the attestation failure error and letting the submission continue") confirm this is deliberate technical debt, not an accidental oversight. The node submits the attestation to the contract unconditionally.

The on-chain contract does perform its own independent DCAP verification via the `tee-verifier` cross-contract call using `dcap_qvl::verify::verify` with the block timestamp: [3](#0-2) 

However, the node-side and contract-side checks are **not equivalent**:

- The node uses `std::time::SystemTime::now()` (wall clock); the contract uses `env::block_timestamp_ms() / 1000` (block time). These can diverge.
- The node uses `default_measurements()` compiled into the binary; the contract uses governance-voted measurements from `TeeState`.
- The node checks `allowed_docker_image_hashes` and `allowed_launcher_compose_hashes` from its local config; the contract checks the on-chain governance-voted lists.

A node whose local validation fails (e.g., its own attestation has an expired certificate, mismatched measurements, or a report-data binding that does not match its actual TLS key) will still submit the attestation. The contract's independent check is the only gate.

### Impact Explanation

The node-side validation is a defense-in-depth layer designed to prevent a node from submitting an attestation it already knows is invalid. By silently discarding the validation result, this layer is completely neutralized. The concrete risks are:

1. **Participant-state invariant broken**: A node running with an expired or measurement-mismatched attestation will still attempt to register as a participant. Until the contract processes and rejects the submission, the node may be treated as a pending or active participant in the signing protocol, corrupting the participant-state accounting.

2. **Timing window for unauthorized participation**: Between the moment the node submits the invalid attestation and the moment the contract's async cross-contract DCAP verification completes and rejects it, the node exists in the contract's `pending_attestations` map. Any signing round that begins in this window may include the node.

3. **Report-data binding bypass attempt**: If a compromised node submits an attestation whose `report_data` does not correctly bind its actual TLS key (i.e., the node is attempting to impersonate another node's identity), the local validation would catch this mismatch. Ignoring the result means the submission proceeds to the contract, which must then be the sole line of defense against identity spoofing.

This maps to the allowed Medium impact: **participant-state and contract execution-flow manipulation that breaks production safety/accounting invariants**.

### Likelihood Explanation

The code path is exercised on every periodic attestation renewal (every 7 days per the TEE lifecycle) and on every attestation resubmission triggered by removal monitoring: [4](#0-3) 

Any node whose local environment diverges from its compiled-in defaults (e.g., after a partial image update, clock skew, or operator misconfiguration of `allowed_docker_image_hashes`) will silently submit a failing attestation. No attacker action is required — the condition arises naturally from normal operations.

### Recommendation

Enforce the validation result before submission. The fix mirrors the recommended mitigation in M-06: check all fields of the returned data before using it.

```rust
pub async fn validate_and_submit_remote_attestation(
    tx_sender: impl TransactionSender,
    attestation: Attestation,
    tls_public_key: Ed25519PublicKey,
    account_public_key: Ed25519PublicKey,
    allowed_docker_image_hashes: &[NodeImageHash],
    allowed_launcher_compose_hashes: &[LauncherDockerComposeHash],
) -> anyhow::Result<()> {
    validate_remote_attestation(
        &attestation,
        tls_public_key.clone(),
        account_public_key,
        allowed_docker_image_hashes,
        allowed_launcher_compose_hashes,
    )?;  // propagate error — do not submit if local validation fails
    submit_remote_attestation(tx_sender, attestation, tls_public_key).await
}
```

If the intent is to submit even when local validation fails (e.g., because the contract is the authoritative verifier), this should be an explicit, documented policy decision with a separate function name that does not imply validation is enforced.

### Proof of Concept

1. Operator deploys a node binary where `default_measurements()` does not match the governance-voted measurements (e.g., after a partial rollout).
2. Node generates a TDX attestation quote and calls `validate_and_submit_remote_attestation`.
3. `validate_remote_attestation` returns `Err(VerificationError::MeasurementsNotAllowed)`.
4. The error is logged as a `WARN` and discarded via `let _ = ...`.
5. `submit_remote_attestation` is called unconditionally, placing the node in `pending_attestations` on the contract.
6. The contract's async DCAP verification runs; depending on whether the contract's allowed-measurements list also excludes these measurements, the attestation is either accepted (node joins with unvalidated measurements) or rejected after a delay (node occupies a pending slot during the verification window).

The node-side validation — the only check that runs synchronously before the submission transaction is broadcast — produces no observable effect on the submission decision. [5](#0-4) [6](#0-5)

### Citations

**File:** crates/node/src/tee/remote_attestation.rs (L93-115)
```rust
fn validate_remote_attestation(
    attestation: &Attestation,
    tls_public_key: Ed25519PublicKey,
    account_public_key: Ed25519PublicKey,
    allowed_docker_image_hashes: &[NodeImageHash],
    allowed_launcher_compose_hashes: &[LauncherDockerComposeHash],
) -> Result<(), VerificationError> {
    let expected_report_data: ReportData =
        ReportDataV1::new(*tls_public_key.as_bytes(), *account_public_key.as_bytes()).into();
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    attestation
        .verify_locally(
            expected_report_data.into(),
            now,
            allowed_docker_image_hashes,
            allowed_launcher_compose_hashes,
            mpc_attestation::attestation::default_measurements(),
        )
        .map(|_| ())
}
```

**File:** crates/node/src/tee/remote_attestation.rs (L117-137)
```rust
pub async fn validate_and_submit_remote_attestation(
    tx_sender: impl TransactionSender,
    attestation: Attestation,
    tls_public_key: Ed25519PublicKey,
    account_public_key: Ed25519PublicKey,
    allowed_docker_image_hashes: &[NodeImageHash],
    allowed_launcher_compose_hashes: &[LauncherDockerComposeHash],
) -> anyhow::Result<()> {
    let _ = validate_remote_attestation(
        &attestation,
        tls_public_key.clone(),
        account_public_key,
        allowed_docker_image_hashes,
        allowed_launcher_compose_hashes,
    )
    .inspect_err(|err| {
        // We could also return here, but for the moment I am just logging the
        // attestation failure error and letting the submission continue
        tracing::warn!("Attestation is not valid: {err}");
    });
    submit_remote_attestation(tx_sender, attestation, tls_public_key).await
```

**File:** crates/tee-verifier/src/lib.rs (L49-63)
```rust
    pub fn verify_quote(
        &self,
        #[serializer(borsh)] quote: QuoteBytes,
        #[serializer(borsh)] collateral: Collateral,
    ) -> VerificationResult {
        let now_seconds = env::block_timestamp_ms() / 1000;
        let quote_bytes: Vec<u8> = quote.into_dcap_type();
        let collateral = collateral.into_dcap_type();
        match dcap_qvl::verify::verify(&quote_bytes, &collateral, now_seconds) {
            Ok(report) => VerificationResult::Verified(report.into_interface_type()),
            Err(err) => {
                VerificationResult::Rejected(VerifierError::DcapVerification(err.to_string()))
            }
        }
    }
```

**File:** docs/tee-lifecycle.md (L186-188)
```markdown
2. **Periodic attestation** — Every 7 days, generates a fresh TDX attestation quote and submits it to the governance contract via [`submit_participant_info()`][submit-participant-info]. Includes exponential backoff retries. (Reference: [`periodic_attestation_submission`][periodic-attestation])

3. **Monitor attestation removal** — Watches the contract for changes to the attested nodes list. If this node's attestation is removed (e.g., due to image hash rotation), resubmits immediately. (Reference: [`monitor_attestation_removal`][monitor-attestation-removal])
```

**File:** crates/mpc-attestation/src/attestation.rs (L474-487)
```rust
fn verify_measurements(
    measurements: &ExpectedMeasurements,
    allowed_measurements: &[ExpectedMeasurements],
) -> Result<(), VerificationError> {
    if allowed_measurements.is_empty() {
        return Err(VerificationError::EmptyMeasurementsList);
    }

    if !allowed_measurements.contains(measurements) {
        return Err(VerificationError::MeasurementsNotAllowed);
    }

    Ok(())
}
```
