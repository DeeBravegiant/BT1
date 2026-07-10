### Title
FanOut Confirmation-Bypass via `NotEnoughBlockConfirmations` Classified as Transient — (`crates/foreign-chain-inspector/src/lib.rs`)

### Summary

`ForeignChainInspectionError::NotEnoughBlockConfirmations` is classified as a **transient** error by `is_transient()`. The `FanOut::extract` aggregation logic only checks for disagreement between `successes` and `non_transient_errors`. A single compromised RPC provider returning `Ok` with inflated confirmations silently overrides all honest providers returning `NotEnoughBlockConfirmations`, causing the FanOut to return `Ok` and the node to issue a threshold signature attesting that a Bitcoin transaction has met its confirmation threshold when it has not.

---

### Finding Description

**Root cause — `is_transient()` misclassifies a security-critical verdict as transient:** [1](#0-0) 

`NotEnoughBlockConfirmations` is listed alongside `ClientError` and `RpcRequestFailed` (genuine network failures). This means a provider's explicit, deterministic rejection of an under-confirmed transaction is treated identically to a timeout.

**Root cause — `FanOut::extract` only guards against `successes` vs `non_transient_errors` disagreement:** [2](#0-1) 

Transient errors are silently discarded before this check. If provider A returns `Ok` and provider B returns `NotEnoughBlockConfirmations`, the `non_transient_errors` vec is empty, `inspectors_split_between_success_and_failure` evaluates to `false`, and execution falls through to: [3](#0-2) 

…which returns `Ok` with the single attacker-controlled success.

**The `BitcoinInspector` emits `NotEnoughBlockConfirmations` as a deterministic, security-enforcing verdict:** [4](#0-3) 

This is not a transient network condition — it is the inspector's authoritative answer that the confirmation threshold was not met.

**The node then builds and signs the payload unconditionally on `Ok`:** [5](#0-4) 

**`respond_verify_foreign_tx` accepts any valid signature over the payload hash, with no re-check of confirmation depth:** [6](#0-5) 

---

### Impact Explanation

The MPC network issues a valid threshold signature over a `ForeignTxSignPayloadV1` that commits to the original `BitcoinRpcRequest` (including the caller-specified `confirmations` field) and the extracted `BlockHash`. A downstream bridge contract that trusts this signature as proof of confirmation depth will release funds for a Bitcoin transaction that has not actually reached the required depth, enabling a double-spend or premature fund release.

This matches: **High — forged foreign-chain verification that causes invalid bridge execution or double-spend conditions.**

---

### Likelihood Explanation

The FanOut is explicitly designed to tolerate a single Byzantine/compromised RPC provider — that is its stated purpose per the doc comment: [7](#0-6) 

The design intent is that one bad provider cannot override honest ones. The bug inverts this: one bad provider *can* override all honest ones for the confirmation check, while honest providers cannot override the bad one. An attacker who can compromise or impersonate a single configured RPC endpoint (e.g., via BGP hijack, DNS poisoning, or a compromised third-party provider) can trigger this with a single crafted `getrawtransaction` response returning inflated `confirmations` and a valid canonical `blockhash`.

---

### Recommendation

`NotEnoughBlockConfirmations` must be reclassified as **non-transient**. It is a deterministic, security-enforcing verdict, not a network failure. Remove it from the `is_transient()` match arm:

```rust
// crates/foreign-chain-inspector/src/lib.rs
pub fn is_transient(&self) -> bool {
    matches!(
        self,
        Self::ClientError(_)
            | Self::RpcRequestFailed(_)
            | Self::NotFinalized
        // NotEnoughBlockConfirmations is intentionally excluded:
        // it is a deterministic rejection, not a transient network failure.
    )
}
```

With this change, if provider A returns `Ok` and provider B returns `NotEnoughBlockConfirmations`, `inspectors_split_between_success_and_failure` becomes `true` and `FanOut` returns `InspectorResponseMismatch`, blocking signing.

---

### Proof of Concept

The existing fanout test infrastructure in `crates/foreign-chain-inspector/tests/fanout.rs` already has all the scaffolding needed. A deterministic integration test:

```rust
#[tokio::test]
async fn fan_out__ok_plus_not_enough_confirmations_should_not_return_ok() {
    // Provider A (attacker): returns Ok
    let attacker = mock_returning(ok(vec![42]));
    // Provider B (honest): returns NotEnoughBlockConfirmations
    let honest = mock_returning(err(|| ForeignChainInspectionError::NotEnoughBlockConfirmations {
        expected: BlockConfirmations::from(6_u64),
        got: BlockConfirmations::from(1_u64),
    }));
    let fan_out = fan_out_of(vec![attacker, honest]);

    let result = fan_out.extract((), (), vec![]).await;

    // Under the current code this assertion FAILS — result is Ok(vec![42])
    assert!(
        !matches!(result, Ok(_)),
        "FanOut must not return Ok when an honest provider rejected the confirmation threshold"
    );
}
```

Under the current code, `result` is `Ok(vec![42])` because `NotEnoughBlockConfirmations` is transient and never enters `non_transient_errors`, so the split-check is never triggered. [8](#0-7)

### Citations

**File:** crates/foreign-chain-inspector/src/lib.rs (L37-57)
```rust
/// Combines multiple inspectors that target the same chain into a single inspector.
///
/// All inner inspectors are queried concurrently. The fan-out treats every
/// non-transient outcome (success or non-transient error, see
/// [`ForeignChainInspectionError::is_transient`]) as a substantive verdict that must
/// agree across inspectors. Transient errors (network issues, finality not yet reached,
/// etc.) are tolerated so that a single unavailable RPC does not take the whole node
/// out of signing.
///
/// Outcomes:
/// * All substantive verdicts are `Ok` with the same extracted values → returns those values.
/// * All substantive verdicts are non-transient errors of the same variant → returns one of
///   them (e.g. all inspectors agree the transaction failed).
/// * Substantive verdicts disagree (`Ok` vs. non-transient error, two different non-transient
///   error variants, or two different success values) → returns
///   [`ForeignChainInspectionError::InspectorResponseMismatch`].
/// * Every inspector returned a transient error → the first such error is propagated.
///
/// Variant-level comparison is used for non-transient errors, so inspectors that all report
/// the same failure mode (e.g. `NonCanonicalBlock`) are considered to agree even if the
/// inner fields differ.
```

**File:** crates/foreign-chain-inspector/src/lib.rs (L118-128)
```rust
        let inspectors_split_between_success_and_failure =
            !successes.is_empty() && !non_transient_errors.is_empty();

        if inspectors_split_between_success_and_failure {
            tracing::error!(
                ?successes,
                ?non_transient_errors,
                "fan-out: inspectors split between success and non-transient failure",
            );
            return Err(ForeignChainInspectionError::InspectorResponseMismatch);
        }
```

**File:** crates/foreign-chain-inspector/src/lib.rs (L130-142)
```rust
        if let Some(first_values) = successes.first() {
            let all_successes_agree = successes.iter().all(|(_, v)| v == &first_values.1);
            if !all_successes_agree {
                tracing::error!(
                    responses = ?successes,
                    "fan-out: inspectors returned mismatching extracted values",
                );
                return Err(ForeignChainInspectionError::InspectorResponseMismatch);
            }
            let (_, first) = successes.into_iter().next().expect("checked non-empty");

            return Ok(first);
        }
```

**File:** crates/foreign-chain-inspector/src/lib.rs (L266-274)
```rust
    pub fn is_transient(&self) -> bool {
        matches!(
            self,
            Self::ClientError(_)
                | Self::RpcRequestFailed(_)
                | Self::NotFinalized
                | Self::NotEnoughBlockConfirmations { .. }
        )
    }
```

**File:** crates/foreign-chain-inspector/src/bitcoin/inspector.rs (L51-59)
```rust
        let enough_block_confirmations =
            block_confirmations_threshold <= transaction_block_confirmation;

        if !enough_block_confirmations {
            return Err(ForeignChainInspectionError::NotEnoughBlockConfirmations {
                expected: block_confirmations_threshold,
                got: transaction_block_confirmation,
            });
        }
```

**File:** crates/node/src/providers/verify_foreign_tx/sign.rs (L337-346)
```rust
        let payload = match payload_version {
            dtos::ForeignTxPayloadVersion::V1 => {
                dtos::ForeignTxSignPayload::V1(dtos::ForeignTxSignPayloadV1 {
                    request: request.clone(),
                    values,
                })
            }
            _ => bail!("unsupported payload_version"),
        };
        Ok(payload)
```

**File:** crates/contract/src/lib.rs (L718-747)
```rust
        let signature_is_valid = match (&response.signature, public_key) {
            (
                dtos::SignatureResponse::Secp256k1(signature_response),
                PublicKeyExtended::Secp256k1 { near_public_key },
            ) => {
                let secp_pk = dtos::Secp256k1PublicKey::try_from(&near_public_key)
                    .expect("Secp256k1 variant always has a secp256k1 key");

                let payload_hash: [u8; 32] = response.payload_hash.0;

                // Check the signature is correct against the root public key
                near_mpc_signature_verifier::verify_ecdsa_signature(
                    signature_response,
                    &payload_hash,
                    &secp_pk,
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
```

**File:** crates/foreign-chain-inspector/tests/fanout.rs (L402-482)
```rust
mod all_transient {
    use super::*;

    #[tokio::test]
    async fn fan_out__should_propagate_transient_when_all_inspectors_fail_with_same_transient_variant()
     {
        // Given
        let make = || mock_returning(err(|| ForeignChainInspectionError::NotFinalized));
        let fan_out = fan_out_of(vec![make(), make()]);

        // When
        let result = fan_out.extract((), (), vec![]).await;

        // Then
        assert_matches!(result, Err(ForeignChainInspectionError::NotFinalized));
    }

    #[tokio::test]
    async fn fan_out__should_propagate_a_transient_when_transient_variants_disagree() {
        // Given: two different transient variants. The fan-out does not gate
        // transient errors on variant agreement, so the result must be transient
        // and must not be InspectorResponseMismatch.
        let a = mock_returning(err(|| ForeignChainInspectionError::NotFinalized));
        let b = mock_returning(err(|| {
            ForeignChainInspectionError::NotEnoughBlockConfirmations {
                expected: BlockConfirmations::from(10_u64),
                got: BlockConfirmations::from(3_u64),
            }
        }));
        let fan_out = fan_out_of(vec![a, b]);

        // When
        let result = fan_out.extract((), (), vec![]).await;

        // Then
        let err = result.expect_err("expected fan-out to return an error");
        assert!(
            err.is_transient(),
            "expected a transient error, got: {err:?}",
        );
        assert!(
            !matches!(err, ForeignChainInspectionError::InspectorResponseMismatch),
            "transient disagreement must not be reported as mismatch, got: {err:?}",
        );
    }

    #[tokio::test]
    async fn fan_out__should_propagate_transient_when_single_inspector_fails_transiently() {
        // Given
        let only = mock_returning(err(|| ForeignChainInspectionError::NotFinalized));
        let fan_out = fan_out_of(vec![only]);

        // When
        let result = fan_out.extract((), (), vec![]).await;

        // Then
        assert_matches!(result, Err(ForeignChainInspectionError::NotFinalized));
    }

    #[tokio::test]
    async fn fan_out__should_propagate_not_enough_block_confirmations_when_all_inspectors_agree() {
        // Given
        let make = || {
            mock_returning(err(|| {
                ForeignChainInspectionError::NotEnoughBlockConfirmations {
                    expected: BlockConfirmations::from(10_u64),
                    got: BlockConfirmations::from(3_u64),
                }
            }))
        };
        let fan_out = fan_out_of(vec![make(), make()]);

        // When
        let result = fan_out.extract((), (), vec![]).await;

        // Then
        assert_matches!(
            result,
            Err(ForeignChainInspectionError::NotEnoughBlockConfirmations { .. })
        );
    }
```
