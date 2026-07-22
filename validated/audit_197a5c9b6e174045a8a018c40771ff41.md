### Title
Silent Discard of L1 Events Provider Errors Causes L1 Handler Transactions to Be Permanently Excluded from Proposed Blocks — (File: `crates/apollo_batcher/src/transaction_provider.rs`)

---

### Summary

`ProposeTransactionProvider::get_l1_handler_txs` wraps every L1 events provider call result in `Ok(...)` after silently converting any error to an empty `Vec` via `.unwrap_or_default()`. The function's declared return type is `TransactionProviderResult<Vec<InternalConsensusTransaction>>`, but it **never returns `Err`**. A downstream phase-transition check then permanently switches the proposer to mempool-only mode for that block, silently excluding all pending L1 handler transactions (L1→L2 messages) from the committed block.

---

### Finding Description

In `ProposeTransactionProvider::get_l1_handler_txs`:

```rust
// crates/apollo_batcher/src/transaction_provider.rs:94-110
async fn get_l1_handler_txs(
    &mut self,
    n_txs: usize,
) -> TransactionProviderResult<Vec<InternalConsensusTransaction>> {
    Ok(self
        .l1_events_provider_client
        .get_txs(n_txs, self.height)
        .await
        .inspect_err(|err| {
            warn!("L1 provider error while fetching L1 handler transactions: {:?}", err);
            BATCHER_L1_EVENTS_PROVIDER_ERRORS.increment(1);
        })
        .unwrap_or_default()   // ← error silently becomes []
        .into_iter()
        .map(InternalConsensusTransaction::L1Handler)
        .collect())
}
```

The error is logged and metered, but then `.unwrap_or_default()` converts it to an empty `Vec`, and the whole expression is wrapped in `Ok(...)`. The function **always returns `Ok`**.

The caller `get_txs` propagates errors with `?`:

```rust
// crates/apollo_batcher/src/transaction_provider.rs:134
let mut l1handler_txs = self.get_l1_handler_txs(n_l1handler_txs_to_get).await?;
```

Because `get_l1_handler_txs` never returns `Err`, the `?` never fires. The caller then evaluates:

```rust
// crates/apollo_batcher/src/transaction_provider.rs:138-142
let no_more_l1handler_in_provider = l1handler_txs.len() < n_l1handler_txs_to_get;
...
if no_more_l1handler_in_provider || reached_max_l1handler_txs_in_block {
    self.phase = TxProviderPhase::Mempool;
}
```

When the L1 provider fails and returns an empty vec, `l1handler_txs.len() == 0 < n_l1handler_txs_to_get`, so `no_more_l1handler_in_provider = true`. The phase permanently switches to `TxProviderPhase::Mempool` for the remainder of the block. All pending L1 handler transactions are silently excluded.

Contrast this with `get_mempool_txs`, which correctly propagates errors with `?`:

```rust
// crates/apollo_batcher/src/transaction_provider.rs:116-123
Ok(self
    .mempool_client
    .get_txs(n_txs)
    .await?   // ← error propagated
    ...
```

The asymmetry is clear: mempool errors abort the proposal; L1 provider errors silently drop all L1 handlers and continue.

The `start_block` discards in `batcher.rs` (lines 386–396 and 493–503) are intentional and documented: "If start_block fails, then subsequent calls to l1 provider will fail on out of session and l1 provider will restart and bootstrap again." This means a `start_block` failure is a **known precursor** that guarantees the subsequent `get_txs` call inside `get_l1_handler_txs` will also fail — and that failure is then silently swallowed. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Impact Explanation

Every time the L1 events provider is unavailable (transient network error, provider restart, out-of-session after a failed `start_block`), the proposer builds and commits a block with **zero L1 handler transactions**, even when pending L1→L2 messages exist. The committed block's state diff will not reflect those L1 message consumptions. This is a wrong-L1-message outcome: valid L1 handler transactions are silently rejected before sequencing, matching the **High** impact category ("Mempool/gateway/RPC admission … rejects valid transactions before sequencing").

---

### Likelihood Explanation

The `start_block` call is explicitly discarded with `let _ =` in both `propose_block` and `validate_block`. The inline comment acknowledges that `start_block` failures are expected and that subsequent L1 provider calls will fail as a result. This makes the error path in `get_l1_handler_txs` reachable on every block where the L1 provider session was not successfully initialized — a routine operational condition, not an exotic edge case. [4](#0-3) 

---

### Recommendation

Propagate the L1 provider error from `get_l1_handler_txs` instead of swallowing it, consistent with how `get_mempool_txs` handles its errors:

```rust
async fn get_l1_handler_txs(
    &mut self,
    n_txs: usize,
) -> TransactionProviderResult<Vec<InternalConsensusTransaction>> {
    self.l1_events_provider_client
        .get_txs(n_txs, self.height)
        .await
        .map_err(|err| {
            warn!("L1 provider error while fetching L1 handler transactions: {:?}", err);
            BATCHER_L1_EVENTS_PROVIDER_ERRORS.increment(1);
            TransactionProviderError::L1EventsProviderError(err)
        })
        .map(|txs| txs.into_iter().map(InternalConsensusTransaction::L1Handler).collect())
}
```

This causes the block builder to abort the proposal on L1 provider failure rather than silently building a block without L1 handlers, preserving the invariant that pending L1→L2 messages are included.

---

### Proof of Concept

1. L1 events provider has a transient error (e.g., network partition, provider restart, or `start_block` was not acknowledged).
2. Proposer enters `get_txs` with `phase == TxProviderPhase::L1`.
3. `get_l1_handler_txs(n)` is called → L1 provider returns `Err(...)`.
4. `.inspect_err` logs the warning; `.unwrap_or_default()` converts `Err` → `[]`; function returns `Ok([])`.
5. Back in `get_txs`: `l1handler_txs.len() == 0 < n`, so `no_more_l1handler_in_provider = true`.
6. Phase switches to `TxProviderPhase::Mempool` permanently for this block.
7. Block is built with zero L1 handler transactions and committed.
8. All pending L1→L2 messages that should have been consumed in this block are silently excluded from the committed state diff. [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_batcher/src/transaction_provider.rs (L94-110)
```rust
    async fn get_l1_handler_txs(
        &mut self,
        n_txs: usize,
    ) -> TransactionProviderResult<Vec<InternalConsensusTransaction>> {
        Ok(self
            .l1_events_provider_client
            .get_txs(n_txs, self.height)
            .await
            .inspect_err(|err| {
                warn!("L1 provider error while fetching L1 handler transactions: {:?}", err);
                BATCHER_L1_EVENTS_PROVIDER_ERRORS.increment(1);
            })
            .unwrap_or_default()
            .into_iter()
            .map(InternalConsensusTransaction::L1Handler)
            .collect())
    }
```

**File:** crates/apollo_batcher/src/transaction_provider.rs (L128-154)
```rust
    async fn get_txs(&mut self, n_txs: usize) -> TransactionProviderResult<NextTxs> {
        assert!(n_txs > 0, "The number of transactions requested must be greater than zero.");
        let mut txs = vec![];
        if self.phase == TxProviderPhase::L1 {
            let n_l1handler_txs_to_get =
                min(self.max_l1_handler_txs_per_block - self.n_l1handler_txs_so_far, n_txs);
            let mut l1handler_txs = self.get_l1_handler_txs(n_l1handler_txs_to_get).await?;
            self.n_l1handler_txs_so_far += l1handler_txs.len();

            // Determine whether we need to switch to mempool phase.
            let no_more_l1handler_in_provider = l1handler_txs.len() < n_l1handler_txs_to_get;
            let reached_max_l1handler_txs_in_block =
                self.n_l1handler_txs_so_far == self.max_l1_handler_txs_per_block;
            if no_more_l1handler_in_provider || reached_max_l1handler_txs_in_block {
                self.phase = TxProviderPhase::Mempool;
            }

            txs.append(&mut l1handler_txs);
            if txs.len() == n_txs {
                return Ok(txs);
            }
        }

        let mut mempool_txs = self.get_mempool_txs(n_txs - txs.len()).await?;
        txs.append(&mut mempool_txs);
        Ok(txs)
    }
```

**File:** crates/apollo_batcher/src/batcher.rs (L384-396)
```rust
        // Ignore errors. If start_block fails, then subsequent calls to l1 provider will fail on
        // out of session and l1 provider will restart and bootstrap again.
        let _ = self
            .l1_events_provider_client
            .start_block(SessionState::Propose, propose_block_input.block_info.block_number)
            .await
            .inspect_err(|err| {
                error!(
                    "L1 provider is not ready to start proposing block {}: {}. ",
                    propose_block_input.block_info.block_number, err
                );
                BATCHER_L1_EVENTS_PROVIDER_ERRORS.increment(1);
            });
```

**File:** crates/apollo_batcher/src/batcher.rs (L491-503)
```rust
        // Ignore errors. If start_block fails, then subsequent calls to l1 provider will fail on
        // out of session and l1 provider will restart and bootstrap again.
        let _ = self
            .l1_events_provider_client
            .start_block(SessionState::Validate, validate_block_input.block_info.block_number)
            .await
            .inspect_err(|err| {
                error!(
                    "L1 provider is not ready to start validating block {}: {}. ",
                    validate_block_input.block_info.block_number, err
                );
                BATCHER_L1_EVENTS_PROVIDER_ERRORS.increment(1);
            });
```
