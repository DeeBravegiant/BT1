The vulnerability claim is **valid**. Here is the full analysis:

---

### Title
Wrong DA Mode in Stale-Pending Fallback Causes Incorrect `estimate_fee` Results for Blob-DA Networks — (`crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

### Summary

When `read_pending_data` falls back to a synthetic `DeprecatedPendingBlock` (because the cached pending data is stale), it omits `l1_da_mode` from the latest block header. The `DeprecatedPendingBlock` variant always returns `L1DataAvailabilityMode::Calldata` from `l1_da_mode()`, causing `create_block_context` to set `use_kzg_da = false` and compute DA costs using calldata pricing instead of blob pricing. Any unprivileged user calling `starknet_estimateFee` or `starknet_simulateTransactions` with `block_id = Tag::Pending` during a stale-pending window receives a wrong fee estimate.

### Finding Description

**Step 1 — Stale fallback construction omits `l1_da_mode`.**

In `read_pending_data`, when `pending_data.block.parent_block_hash() != latest_header.block_hash`, the code constructs a synthetic `DeprecatedPendingBlock` from the latest header fields. It copies `l1_gas_price`, `timestamp`, `sequencer_address`, and `starknet_version`, but **does not copy `l1_da_mode`** because `DeprecatedPendingBlock` has no such field: [1](#0-0) 

**Step 2 — `l1_da_mode()` on `DeprecatedPendingBlock` always returns `Calldata`.** [2](#0-1) 

**Step 3 — `l1_data_gas_price()` on `DeprecatedPendingBlock` also returns zero.** [3](#0-2) 

**Step 4 — Conversion to `ExecutionPendingData` propagates the wrong `l1_da_mode`.**

`client_pending_data_to_execution_pending_data` calls `block.l1_da_mode()` directly, so the `Calldata` value flows into the execution layer: [4](#0-3) 

**Step 5 — `create_block_context` sets `use_kzg_da = false`.** [5](#0-4) 

Because `l1_da_mode.is_use_kzg_da()` returns `false` for `Calldata`, `use_kzg_da` is set to `false` even when the actual network is operating in Blob/KZG DA mode.

**Step 6 — `estimate_fee` with `Tag::Pending` uses this wrong context.** [6](#0-5) 

The `DONT_IGNORE_L1_DA_MODE` flag (i.e., `override_kzg_da_to_false = false`) means the wrong `l1_da_mode` from the stale fallback is the sole source of truth for `use_kzg_da`.

### Impact Explanation

On a Blob-DA network (`l1_da_mode = Blob` in the latest block header):
- The stale fallback forces `use_kzg_da = false` and `l1_data_gas_price = 0`.
- DA costs are computed as L1 calldata gas instead of L1 data gas (blob pricing).
- The returned `overall_fee`, `l1_gas_consumed`, and `l1_data_gas_consumed` are all wrong.
- Transactions sized by this estimate may fail on-chain (under-estimated) or overpay (over-estimated depending on relative prices).

This matches the allowed impact: **"High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."**

### Likelihood Explanation

The stale-pending window is a **normal operational state**, not an edge case. It occurs:
- On every node restart before the first pending sync.
- Whenever the pending data sync lags behind block finalization.
- Any time the feeder gateway is temporarily unreachable.

No special privileges are required. Any user calling `starknet_estimateFee` with `block_id = Tag::Pending` during this window is affected.

### Recommendation

In the stale fallback branch of `read_pending_data`, use `PendingBlockOrDeprecated::Current(PendingBlock { ... })` instead of `DeprecatedPendingBlock`, and populate `l1_da_mode` from `latest_header.block_header_without_hash.l1_da_mode` and `l1_data_gas_price` from `latest_header.block_header_without_hash.l1_data_gas_price`. This mirrors how the non-stale path already carries the correct `l1_da_mode` from the live `PendingBlock`.

### Proof of Concept

1. Configure a node on a Blob-DA network (latest block header has `l1_da_mode = Blob`).
2. Ensure `pending_data` is stale (parent hash mismatch — e.g., immediately after a new block is finalized before the pending sync updates).
3. Call `starknet_estimateFee` with `block_id = Tag::Pending` for a transaction with non-trivial state writes.
4. Observe that the returned `l1_data_gas_consumed` is `0` and `l1_gas_consumed` includes DA costs as calldata gas.
5. Call the same `starknet_estimateFee` with `block_id = Tag::Latest` (which reads `l1_da_mode` from the stored header correctly).
6. The two results differ in DA cost breakdown, confirming the wrong mode was used in step 4.

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1009-1016)
```rust
        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
            None
        };
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1573-1594)
```rust
        Ok(PendingData {
            block: PendingBlockOrDeprecated::Deprecated(DeprecatedPendingBlock {
                parent_block_hash: latest_header.block_hash,
                eth_l1_gas_price: latest_header.block_header_without_hash.l1_gas_price.price_in_wei,
                strk_l1_gas_price: latest_header
                    .block_header_without_hash
                    .l1_gas_price
                    .price_in_fri,
                timestamp: latest_header.block_header_without_hash.timestamp,
                sequencer_address: latest_header.block_header_without_hash.sequencer,
                starknet_version: latest_header
                    .block_header_without_hash
                    .starknet_version
                    .to_string(),
                ..Default::default()
            }),
            state_update: ClientPendingStateUpdate {
                old_root: latest_header.block_header_without_hash.state_root,
                state_diff: Default::default(),
            },
        })
    }
```

**File:** crates/apollo_starknet_client/src/reader/objects/pending_data.rs (L155-161)
```rust
    pub fn l1_data_gas_price(&self) -> GasPricePerToken {
        match self {
            // In older versions, data gas price was 0.
            PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(),
            PendingBlockOrDeprecated::Current(block) => block.l1_data_gas_price,
        }
    }
```

**File:** crates/apollo_starknet_client/src/reader/objects/pending_data.rs (L169-175)
```rust
    pub fn l1_da_mode(&self) -> L1DataAvailabilityMode {
        match self {
            // In older versions, all blocks were using calldata.
            PendingBlockOrDeprecated::Deprecated(_) => L1DataAvailabilityMode::Calldata,
            PendingBlockOrDeprecated::Current(block) => block.l1_da_mode,
        }
    }
```

**File:** crates/apollo_rpc/src/pending.rs (L17-22)
```rust
        timestamp: client_pending_data.block.timestamp(),
        l1_gas_price: client_pending_data.block.l1_gas_price(),
        l1_data_gas_price: client_pending_data.block.l1_data_gas_price(),
        l2_gas_price: client_pending_data.block.l2_gas_price(),
        l1_da_mode: client_pending_data.block.l1_da_mode(),
        sequencer: client_pending_data.block.sequencer_address(),
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L369-369)
```rust
    let use_kzg_da = if override_kzg_da_to_false { false } else { l1_da_mode.is_use_kzg_da() };
```
