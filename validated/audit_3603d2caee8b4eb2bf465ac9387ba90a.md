### Title
`GlobalContractDistribution` Receipt with Stale Target Shard Causes Chain-Halting Panic in `receipt_filter_fn` - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary

`DelayedReceiptQueueWrapper::receipt_filter_fn` calls `.unwrap()` on the result of `Receipt::receiver_shard_id()`. For `GlobalContractDistribution` receipts whose embedded `target_shard` no longer exists in the current shard layout and cannot be resolved through the layout's split history, `receiver_shard_id()` returns `Err`. The unconditional `.unwrap()` then panics, stalling chunk application on every node that processes the delayed receipt queue — an exact structural analog to the Mosaic bug where `coverWithdrawRequest` calls `withdrawInvestment` with an array length that one specific implementation cannot handle, causing a revert in a critical path.

### Finding Description

**Caller assumption (analog to `coverWithdrawRequest`):**

`receipt_filter_fn` in `DelayedReceiptQueueWrapper` is called for every receipt popped from the delayed queue. It unconditionally unwraps the result of `receiver_shard_id()`:

```rust
fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
    let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
    let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
    receipt_shard_id == self.shard_id
}
``` [1](#0-0) 

This function is invoked from both `pop()` and `peek_iter()`, which are on the hot path of every chunk application. [2](#0-1) 

**Callee divergence (analog to `SushiswapLiquidityProvider.withdrawInvestment`):**

`receiver_shard_id()` handles `GlobalContractDistribution` receipts differently from all other receipt types. For ordinary receipts it maps `receiver_id` to a shard via `account_id_to_shard_id`, which always succeeds. For `GlobalContractDistribution` receipts it uses the receipt's embedded `target_shard` field. If that shard ID is absent from the current layout **and** `resolve_to_current_shard` returns `None` (which happens with static/V1–V2 shard layouts that do not maintain a full split history), the function returns `Err`:

```rust
ReceiptEnum::GlobalContractDistribution(receipt) => {
    let target_shard = receipt.target_shard();
    if shard_layout.shard_ids().contains(&target_shard) {
        target_shard
    } else {
        let Some(current_shard) = shard_layout.resolve_to_current_shard(target_shard)
        else {
            return Err(EpochError::ShardingError(format!(
                "Shard {target_shard} does not exist in the shard layout or its split history",
            )));
        };
        current_shard
    }
}
``` [3](#0-2) 

The `.unwrap()` in `receipt_filter_fn` then panics.

**How the stale receipt enters the delayed queue:**

When a user submits a `DeployGlobalContract` action, the runtime emits a `GlobalContractDistributionReceipt` whose `target_shard` is set to the deployer's current shard. If the chunk's compute budget is exhausted before the receipt is processed, it is pushed to the persistent delayed-receipt queue. If one or more resharding events then occur before the queue drains, the stored `target_shard` may refer to a shard that no longer exists in the new layout. With static (V1/V2) shard layouts, `resolve_to_current_shard` cannot trace the lineage and returns `None`, triggering the panic.

The test `test_stale_global_contract_distribution_after_double_resharding` explicitly documents this failure mode and its comment confirms the fix only covers V3 (dynamic) shard layouts:

> "The fix only works with V3 shard layouts (dynamic resharding). With static resharding, the shard layout doesn't maintain a full split history."
> "If the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in receipt_filter_fn() when receiver_shard_id() fails to remap the old target_shard after two resharding generations." [4](#0-3) 

### Impact Explanation

A panic inside `receipt_filter_fn` propagates through `DelayedReceiptQueueWrapper::pop` and `peek_iter`, aborting chunk application. Because every honest chunk producer and chunk validator replays the same delayed queue, all nodes processing that shard's chunk will panic on the same receipt. The chain head for that shard stops advancing — a consensus-level stall equivalent to the Mosaic scenario where Alice's withdrawal is permanently blocked. The corrupted protocol value is the **finality/head** of the affected shard: no new chunk can be certified until the stale receipt is somehow removed from the queue, which requires a protocol-level intervention.

### Likelihood Explanation

An unprivileged user can:
1. Submit a `DeployGlobalContract` transaction (no special permission required).
2. Flood the target shard with compute-heavy transactions to saturate the chunk gas limit, forcing the `GlobalContractDistribution` receipt into the delayed queue.
3. Wait for one or more resharding events (which occur automatically under dynamic resharding or can be anticipated under static resharding schedules).

Under static resharding (V1/V2 layouts), the vulnerability is unconditionally reachable once the receipt is delayed across a single resharding boundary. Under dynamic resharding (V3 layouts), `resolve_to_current_shard` mitigates the panic for well-formed split histories, but the `.unwrap()` remains and would fire if the history is incomplete or corrupted.

### Recommendation

Replace the `.unwrap()` in `receipt_filter_fn` with proper error propagation. Change the return type to `Result<bool, RuntimeError>` and propagate the `EpochError` upward so that chunk application returns a recoverable `RuntimeError` rather than panicking. Additionally, add a protocol-level guard that skips or re-routes `GlobalContractDistribution` receipts whose `target_shard` cannot be resolved, rather than treating the resolution failure as an invariant violation.

### Proof of Concept

1. On a network using static (V1/V2) shard layouts, submit a `DeployGlobalContract` transaction from account `user0` whose shard is `S_A`.
2. Simultaneously submit enough compute-heavy transactions to saturate `S_A`'s chunk gas limit, forcing the emitted `GlobalContractDistributionReceipt` (with `target_shard = S_A`) into the delayed queue.
3. Allow one resharding event to split `S_A` into `S_A1` and `S_A2`. The delayed receipt now holds a `target_shard` that no longer exists.
4. Stop the compute saturation. On the next chunk application, `DelayedReceiptQueueWrapper::pop` calls `receipt_filter_fn`, which calls `receiver_shard_id(&new_layout).unwrap()`. Because `S_A` is absent from the new layout and `resolve_to_current_shard` returns `None` for V1/V2 layouts, `receiver_shard_id` returns `Err`, and `.unwrap()` panics.
5. All nodes processing that shard's chunk panic on the same receipt; the chain head for that shard stalls. [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L904-907)
```rust
            // Track gas and bytes for receipt above and return only receipt that belong to the shard.
            if self.receipt_filter_fn(&receipt) {
                return Ok(Some(receipt));
            }
```

**File:** core/primitives/src/receipt.rs (L437-465)
```rust
    pub fn receiver_shard_id(&self, shard_layout: &ShardLayout) -> Result<ShardId, EpochError> {
        let shard_id = match self.receipt() {
            ReceiptEnum::Action(_)
            | ReceiptEnum::ActionV2(_)
            | ReceiptEnum::Data(_)
            | ReceiptEnum::PromiseYield(_)
            | ReceiptEnum::PromiseYieldV2(_)
            | ReceiptEnum::PromiseResume(_) => {
                shard_layout.account_id_to_shard_id(self.receiver_id())
            }
            ReceiptEnum::GlobalContractDistribution(receipt) => {
                let target_shard = receipt.target_shard();
                if shard_layout.shard_ids().contains(&target_shard) {
                    target_shard
                } else {
                    // The target shard may be from an arbitrarily old layout (the receipt could
                    // have been delayed across multiple resharding events). resolve_to_current_shard
                    // will find a shard descendant in the current layout.
                    let Some(current_shard) = shard_layout.resolve_to_current_shard(target_shard)
                    else {
                        return Err(EpochError::ShardingError(format!(
                            "Shard {target_shard} does not exist in the shard layout or its split history",
                        )));
                    };
                    current_shard
                }
            }
        };
        Ok(shard_id)
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L32-39)
```rust
fn test_stale_global_contract_distribution_after_double_resharding() {
    init_test_logger();

    // The fix only works with V3 shard layouts (dynamic resharding).
    // With static resharding, the shard layout doesn't maintain a full split history.
    if !ProtocolFeature::DynamicResharding.enabled(PROTOCOL_VERSION) {
        return;
    }
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L165-185)
```rust
    // Step 4: Stop saturating. Let the delayed queue drain.
    // If the vulnerability exists, processing the stale GlobalContractDistribution
    // receipt will panic in receipt_filter_fn() when receiver_shard_id() fails
    // to remap the old target_shard after two resharding generations.
    let current_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };
    let drain_end = current_height + epoch_length * 2;
    env.runner_for_account(&chunk_producer).run_until_head_height(drain_end);

    let head_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };
    assert!(
        head_height >= drain_end,
        "chain stalled at height {}; expected >= {} (likely panicked processing stale receipt)",
        head_height,
        drain_end
    );
```
