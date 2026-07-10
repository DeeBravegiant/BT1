### Title
`ClientError` Misclassified as Transient Enables Single-Provider Bypass of Bitcoin Block Canonicality Verification — (`crates/foreign-chain-inspector/src/lib.rs`, `crates/foreign-chain-inspector/src/bitcoin/inspector.rs`)

---

### Summary

`ForeignChainInspectionError::ClientError` — which wraps raw `jsonrpsee` errors including JSON-RPC application-level errors such as `"Block not found"` — is classified as **transient** by `is_transient()`. The `FanOut` aggregator silently discards transient errors as non-substantive. Consequently, when one provider returns `Ok(BlockHash(B))` and a second provider returns `ClientError("Block not found")` for `getblockheader(B)` (because block B was reorganized away), the `FanOut` accepts the single success without triggering `InspectorResponseMismatch`. The MPC network then signs a payload anchored to a potentially non-canonical block, bypassing the multi-provider quorum.

---

### Finding Description

**Root cause — `is_transient` over-classifies `ClientError`:** [1](#0-0) 

`ClientError` wraps the raw `jsonrpsee::core::client::error::Error`, which includes both transport-level failures *and* JSON-RPC application-level error responses (error code + message from the server). A `"Block not found"` response from `getblockheader` is a deterministic, non-retryable signal that the block does not exist in the provider's view — yet it surfaces as `ClientError` and is therefore treated as transient.

The `bitcoin/inspector.rs` code explicitly acknowledges this: [2](#0-1) 

**`FanOut::extract` discards transient errors as non-substantive:** [3](#0-2) 

Transient errors are placed in `first_transient_error` and never in `non_transient_errors`. The split-detection guard only fires when `!successes.is_empty() && !non_transient_errors.is_empty()`: [4](#0-3) 

So with provider-1 → `Ok(BlockHash(B))` and provider-2 → `ClientError("Block not found")`:
- `successes = [(0, [BlockHash(B)])]`
- `non_transient_errors = []`
- `inspectors_split_between_success_and_failure = false`
- `all_successes_agree = true` (single entry)
- Returns `Ok([BlockHash(B)])` — **single-provider success accepted**

**The canonicality check that should catch this:** [5](#0-4) 

`verify_block_is_canonical` calls `getblockheader(B)` then `getblockhash(H)`. If `getblockheader` returns `"Block not found"`, the `?` propagates a `ClientError` — not `NonCanonicalBlock`. This is the exact path that gets swallowed as transient by `FanOut`.

---

### Impact Explanation

An attacker who controls one of the configured Bitcoin RPC providers can cause the MPC network to produce a threshold signature over a `ForeignTxSignPayload` that attests to a transaction anchored in a reorganized (non-canonical) block. This constitutes **forged foreign-chain verification**: the signed payload can be used to claim bridge funds on NEAR for a Bitcoin transaction that no longer exists on the canonical chain, enabling a double-spend.

This matches the allowed High impact: *"forged foreign-chain verification … that causes invalid bridge execution or double-spend conditions."*

---

### Likelihood Explanation

The attack requires three concurrent conditions:

1. **A Bitcoin reorg** (natural 1-block reorgs occur several times per year; deeper ones are rarer but not impossible).
2. **Provider-1 is stale** — it still indexes block B after the reorg. This is a natural race window of seconds to minutes depending on the provider's sync speed.
3. **Attacker controls provider-2** — they make it return `ClientError("Block not found")` instead of the `NonCanonicalBlock` that an honest, updated provider would return via `getblockhash(H) ≠ B`.

Without controlling provider-2, the same race can occur naturally if provider-2 is a node that prunes reorganized blocks from its index (common for pruned nodes and many hosted RPC services), since `getblockheader(B)` would genuinely return `"Block not found"` for a pruned reorganized block.

Likelihood is **low-to-medium**: requires a reorg window and either a compromised provider or a pruning provider configuration.

---

### Recommendation

1. **Distinguish application-level RPC errors from transport errors.** Parse the `jsonrpsee` error to detect JSON-RPC error codes/messages. Map `"Block not found"` (and similar deterministic server-side rejections) to a dedicated non-transient variant (e.g., `BlockNotFound`) rather than leaving them as `ClientError`.

2. **Classify `ClientError` as non-transient by default**, or introduce a sub-classification. Only transport errors (connection refused, timeout, TLS failure) should be transient; application-level JSON-RPC errors are deterministic and must be treated as substantive verdicts.

3. **Add a `FanOut` unit test** with two mock `BitcoinInspector`s where one returns `Ok(BlockHash(B))` and the other returns `ClientError("Block not found")`; assert the result is `InspectorResponseMismatch`, not `Ok`.

---

### Proof of Concept

Trace through `FanOut::extract` with two providers:

```
provider-1.extract(tx, finality, extractors) → Ok([BlockHash(B)])
provider-2.extract(tx, finality, extractors) → Err(ClientError("Block not found"))
```

Inside `FanOut::extract`:
- `ClientError(...).is_transient()` → `true` → goes to `first_transient_error`, not `non_transient_errors`
- `inspectors_split_between_success_and_failure` = `![(0,…)].is_empty() && ![].is_empty()` = `false`
- `successes.first()` = `Some((0, [BlockHash(B)]))`; `all_successes_agree` = `true`
- **Returns `Ok([BlockHash(B)])`** — provider-2's canonicality failure is silently discarded

The invariant *"block hash must be confirmed canonical by the configured quorum of providers"* is broken: a single provider's stale or attacker-controlled response unilaterally determines the signed payload. [6](#0-5) [7](#0-6)

### Citations

**File:** crates/foreign-chain-inspector/src/lib.rs (L96-116)
```rust
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

**File:** crates/foreign-chain-inspector/src/lib.rs (L130-141)
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
```

**File:** crates/foreign-chain-inspector/src/lib.rs (L265-274)
```rust
impl ForeignChainInspectionError {
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

**File:** crates/foreign-chain-inspector/src/bitcoin/inspector.rs (L91-93)
```rust
    /// Failures from the RPCs themselves ("Block not found" / "block height out of range")
    /// surface as `ClientError` rather than `NonCanonicalBlock`; mapping those error
    /// messages to a more specific variant is left to a follow-up.
```

**File:** crates/foreign-chain-inspector/src/bitcoin/inspector.rs (L94-132)
```rust
    async fn verify_block_is_canonical(
        &self,
        receipt_blockhash: TransportBitcoinBlockHash,
    ) -> Result<(), ForeignChainInspectionError> {
        let get_block_header_args = GetBlockHeaderArgs {
            blockhash: receipt_blockhash,
            verbose: VERBOSE_RESPONSE,
        };
        let block: GetBlockHeaderVerboseResponse = self
            .client
            .request(GET_BLOCK_HEADER_METHOD, &get_block_header_args)
            .await?;

        // Defensive: `getblockheader` looks the header up *by hash*, so a well-behaved backend
        // always echoes back the hash we queried
        if block.hash != receipt_blockhash {
            return Err(ForeignChainInspectionError::InconsistentRpcResponse {
                requested_hash: (*receipt_blockhash).to_vec().into(),
                returned_hash: (*block.hash).to_vec().into(),
            });
        }

        let get_block_hash_args = GetBlockHashArgs {
            height: block.height,
        };
        let canonical_blockhash: TransportBitcoinBlockHash = self
            .client
            .request(GET_BLOCK_HASH_METHOD, &get_block_hash_args)
            .await?;

        if canonical_blockhash != receipt_blockhash {
            return Err(non_canonical_block_error(
                block.height,
                receipt_blockhash,
                canonical_blockhash,
            ));
        }
        Ok(())
    }
```
