### Title
Epoch-Boundary Shard-ID Mismatch in `congestion_control_accepts_transaction` Bypasses Congestion Control During Dynamic Resharding — (`chain/chain/src/runtime/mod.rs`)

---

### Summary

`congestion_control_accepts_transaction` resolves the receiver's shard using `next_epoch_id` (the new epoch's child-shard layout), but looks up congestion state in `prev_block.congestion_info`, which is keyed by the **previous epoch's parent-shard IDs**. At the first block of a resharding epoch boundary, the child shard ID is absent from the map, the lookup returns `None`, and the function unconditionally returns `Ok(true)` — admitting transactions to a congested shard that should be rejected.

---

### Finding Description

**Root cause — epoch/layout mismatch:**

Inside `prepare_transactions_extra`, the working epoch is set to `prev_block.next_epoch_id`: [1](#0-0) 

This is the epoch of the block being produced (epoch N+1 after resharding). `congestion_control_accepts_transaction` is then called with this `epoch_id`: [2](#0-1) 

Inside the function, the receiver's shard is resolved using epoch N+1's shard layout (which has child shards), then looked up in `prev_block.congestion_info`: [3](#0-2) 

**`prev_block.congestion_info` is keyed by epoch N's shard IDs (parent shards):**

In the non-SPICE path, `congestion_info` is `prev_block.block_congestion_info()`, which iterates chunk headers and inserts by `chunk.shard_id()` — the parent shard IDs from epoch N: [4](#0-3) 

`PrepareTransactionsBlockContext::new` sets `next_epoch_id` via `get_epoch_id_from_prev_block`, which returns epoch N+1 when `prev_block` is the last block of epoch N: [5](#0-4) 

**The mismatch:**

| Field | Epoch | Shard IDs |
|---|---|---|
| `epoch_id` passed to `account_id_to_shard_id` | N+1 | child shard IDs |
| `prev_block.congestion_info` keys | N | parent shard IDs |

`congestion_info.get(&child_shard_id)` returns `None`. The `None` arm unconditionally returns `Ok(true)`: [6](#0-5) 

The SPICE path's `build_block_congestion_info` even has an explicit TODO acknowledging this is unhandled: [7](#0-6) 

---

### Impact Explanation

During the first chunk produced at the start of epoch N+1 (immediately after a resharding event), any transaction whose `receiver_id` maps to a new child shard bypasses the congestion check entirely. If the parent shard was congested (above `reject_tx_congestion_threshold` = 0.8), those transactions should be rejected but are instead admitted. The admitted transactions generate receipts forwarded to the already-congested child shard, worsening congestion and potentially causing cascading backpressure across the network. The `chunk_tx_gas_limit` function has the same `None → default (no congestion)` fallback for the local shard: [8](#0-7) 

This means both the per-chunk gas budget and the per-receiver congestion gate are simultaneously uncapped at the epoch boundary.

---

### Likelihood Explanation

Dynamic resharding is a production feature (enabled via `ShardLayoutConfig::Dynamic`). Resharding is triggered automatically when memory usage exceeds `memory_usage_threshold`. An attacker does not need to trigger resharding — they only need to observe the epoch boundary (publicly visible on-chain) and submit transactions via the public RPC with a `receiver_id` that maps to a new child shard. No validator or operator privileges are required. The exploit window is one chunk (the first chunk of epoch N+1), but that is sufficient to admit a batch of transactions that should have been rejected.

---

### Recommendation

In `congestion_control_accepts_transaction`, when the child shard ID is not found in `prev_block.congestion_info`, resolve the parent shard ID using the previous epoch's layout and fall back to the parent's congestion entry. Concretely:

1. When `congestion_info.get(&receiving_shard)` returns `None`, call `epoch_manager.get_prev_shard_id_from_prev_hash` (already used in `build_block_congestion_info`) to find the parent shard ID.
2. Retry the lookup with the parent shard ID.
3. Only return `Ok(true)` if the parent lookup also returns `None` (genuinely new shard with no history).

The same fix should be applied to `chunk_tx_gas_limit` for the local shard's gas budget.

---

### Proof of Concept

**Setup:**
1. Enable dynamic resharding with a low `memory_usage_threshold` so a split is triggered at epoch boundary N→N+1.
2. Congest the parent shard (e.g., shard 0) above `reject_tx_congestion_threshold` (0.8) by flooding it with receipts.
3. At the last block of epoch N, verify that `prev_block.congestion_info` contains `shard_0` with congestion level ≥ 0.8.

**Trigger:**
4. At the first block of epoch N+1, submit a transaction via public RPC with `receiver_id = "boundary_account"` where `shard_layout_N+1.account_id_to_shard_id("boundary_account") = child_shard_A` (a new child shard not present in epoch N's layout).

**Observed:**
5. `congestion_control_accepts_transaction` calls `account_id_to_shard_id(receiver_id, epoch_N+1)` → `child_shard_A`.
6. `prev_block.congestion_info.get(&child_shard_A)` → `None`.
7. Returns `Ok(true)` — transaction is admitted to the chunk.

**Expected:**
8. The function should resolve `child_shard_A`'s parent (`shard_0`), find its congestion entry (level ≥ 0.8), and return `Ok(false)` — transaction rejected.

### Citations

**File:** chain/chain/src/runtime/mod.rs (L909-909)
```rust
        let epoch_id = prev_block.next_epoch_id;
```

**File:** chain/chain/src/runtime/mod.rs (L1032-1038)
```rust
                if !congestion_control_accepts_transaction(
                    self.epoch_manager.as_ref(),
                    &runtime_config,
                    &epoch_id,
                    &prev_block,
                    &validated_tx,
                )? {
```

**File:** chain/chain/src/runtime/mod.rs (L1684-1688)
```rust
    // The own congestion may be None when a new shard is created, or when the
    // feature is just being enabled. Using the default (no congestion) is a
    // reasonable choice in this case.
    let own_congestion = prev_block.congestion_info.get(&shard_id).cloned();
    let own_congestion = own_congestion.unwrap_or_default();
```

**File:** chain/chain/src/runtime/mod.rs (L1708-1712)
```rust
    let receiver_id = validated_tx.receiver_id();
    let receiving_shard = account_id_to_shard_id(epoch_manager, receiver_id, &epoch_id)?;
    let congestion_info = prev_block.congestion_info.get(&receiving_shard);
    let Some(congestion_info) = congestion_info else {
        return Ok(true);
```

**File:** core/primitives/src/block.rs (L770-787)
```rust
    pub fn block_congestion_info(&self) -> BlockCongestionInfo {
        let mut result = BTreeMap::new();

        for chunk in self.iter_raw() {
            let shard_id = chunk.shard_id();

            let congestion_info = chunk.congestion_info();
            let height_included = chunk.height_included();
            let height_current = self.block_height;
            let missed_chunks_count = height_current.checked_sub(height_included);
            let missed_chunks_count = missed_chunks_count
                .expect("The chunk height included must be less or equal than block height!");

            let extended_congestion_info =
                ExtendedCongestionInfo::new(congestion_info, missed_chunks_count);
            result.insert(shard_id, extended_congestion_info);
        }
        BlockCongestionInfo::new(result)
```

**File:** chain/chain/src/types.rs (L503-516)
```rust
impl PrepareTransactionsBlockContext {
    pub fn new(
        prev_block: &Block,
        epoch_manager: &dyn EpochManagerAdapter,
        congestion_info: BlockCongestionInfo,
    ) -> Result<Self, Error> {
        let header = prev_block.header();
        Ok(Self {
            next_gas_price: header.next_gas_price(),
            height: header.height(),
            next_epoch_id: epoch_manager.get_epoch_id_from_prev_block(&header.hash())?,
            congestion_info,
        })
    }
```

**File:** chain/chain/src/spice/chunk_application.rs (L326-327)
```rust
    // TODO(spice-resharding): across a resharding boundary both children map to the
    // same parent and inherit its congestion info unsplit. See dynamic_resharding.md.
```
