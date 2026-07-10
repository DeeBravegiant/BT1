### Title
Single-Provider Forgery via Transient-Error Quorum Bypass in `FanOut::extract` — (`crates/foreign-chain-inspector/src/lib.rs`)

---

### Summary

`FanOut::extract` enforces agreement only among *non-transient* verdicts. Because `NotFinalized` and `NotEnoughBlockConfirmations` are classified as transient, a single attacker-controlled provider that returns a forged `Ok(values)` while every honest provider returns a naturally-occurring transient error is accepted unconditionally. There is no minimum-quorum gate: one success plus N−1 transient errors is treated identically to unanimous success.

---

### Finding Description

`FanOut::extract` partitions results into three buckets: [1](#0-0) 

The only cross-provider consistency check fires when **both** `successes` and `non_transient_errors` are non-empty: [2](#0-1) 

If `non_transient_errors` is empty (all honest providers returned transient errors), execution falls through to: [3](#0-2) 

`all_successes_agree` is trivially `true` when `successes` has exactly one entry, so the attacker's forged `Vec<ExtractedValue>` is returned as the authoritative result. There is no floor on how many providers must agree.

The transient classification includes two **naturally-occurring, non-adversarial** states: [4](#0-3) 

`NotFinalized` and `NotEnoughBlockConfirmations` are returned by honest providers for any real transaction that is still inside the finality window — a condition that exists for every transaction for some period of time, requiring no attacker action against the honest providers.

---

### Impact Explanation

During the finality window of a real foreign-chain transaction T:

1. Honest providers return `NotFinalized` / `NotEnoughBlockConfirmations` (transient) for T.
2. The attacker's provider returns `Ok(forged_values)` — e.g., a different token amount, recipient, or block hash — for the same T.
3. `FanOut` on each MPC node that has the attacker's provider in its config returns the forged values.
4. If the attacker's provider is listed in enough nodes' configs to reach the signing threshold, all those nodes sign the same forged payload.
5. A valid threshold signature is produced over a forged foreign-chain observation, enabling invalid bridge execution.

Impact: **forged foreign-chain verification → invalid bridge execution / double-spend** (High per scope).

---

### Likelihood Explanation

- The attacker must be one of the configured RPC providers for the target chain on enough MPC nodes to reach the signing threshold. This is a meaningful precondition, but widely-used commercial RPC providers (Alchemy, QuickNode, etc.) are typically shared across all operator nodes.
- No DDoS is required. `NotFinalized` is a natural, guaranteed transient state for every transaction during its finality window.
- The attack window is bounded by the finality period (seconds to minutes depending on chain), but the attacker controls their provider and can time the forged response precisely.

---

### Recommendation

Add a **minimum-quorum gate** before accepting any success result. The number of non-transient successes must meet a configurable threshold (e.g., `ceil(n/2) + 1` or a fixed operator-set value) before the result is trusted:

```rust
// After collecting results, before returning Ok:
let required_quorum = (self.inspectors.len() / 2) + 1;
if successes.len() < required_quorum {
    return Err(ForeignChainInspectionError::InsufficientQuorum {
        got: successes.len(),
        required: required_quorum,
    });
}
```

`InsufficientQuorum` should be classified as **transient** so the node retries once more providers become available, rather than treating it as a hard failure.

---

### Proof of Concept

```rust
// Unit test: 1 success + 2 transient errors must NOT return Ok.
// Currently this test FAILS (FanOut returns Ok with the single success).
#[tokio::test]
async fn single_success_with_transient_errors_is_rejected() {
    let inspectors = NonEmptyVec::try_from(vec![
        MockInspector::success(vec!["forged_value"]),   // attacker's provider
        MockInspector::transient_error(),               // NotFinalized
        MockInspector::transient_error(),               // NotFinalized
    ]).unwrap();
    let fanout = FanOut::new(inspectors);
    let result = fanout.extract(tx_id(), finality(), extractors()).await;
    // Invariant: a single provider must not be sufficient.
    assert!(result.is_err(), "expected error, got {:?}", result);
}
```

With the current code at lines 130–141, `successes.len() == 1`, `all_successes_agree` is trivially `true`, and `Ok("forged_value")` is returned — the invariant is violated. [5](#0-4)

### Citations

**File:** crates/foreign-chain-inspector/src/lib.rs (L92-116)
```rust
        let mut successes: Vec<(usize, Vec<Self::ExtractedValue>)> = Vec::new();
        let mut non_transient_errors: Vec<(usize, ForeignChainInspectionError)> = Vec::new();
        let mut first_transient_error: Option<ForeignChainInspectionError> = None;

        for (idx, result) in join_set.join_all().await {
            match result {
                Ok(values) => successes.push((idx, values)),
                Err(err) if err.is_transient() => {
                    tracing::warn!(
                        inspector_index = idx,
                        error = %err,
                        "fan-out inspector failed (transient)",
                    );
                    first_transient_error.get_or_insert(err);
                }
                Err(err) => {
                    tracing::error!(
                        inspector_index = idx,
                        error = %err,
                        "fan-out inspector failed (non-transient)",
                    );
                    non_transient_errors.push((idx, err));
                }
            }
        }
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
