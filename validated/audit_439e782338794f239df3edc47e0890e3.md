### Title
Missing Duplicate Boundary Account Validation in Dynamic Resharding Proposal Causes Chain Halt at Epoch Finalization - (File: chain/chain/src/runtime/mod.rs)

### Summary
The `check_dynamic_resharding` function computes a proposed shard split boundary account via `find_trie_split` but does not validate that the resulting boundary account is absent from the current shard layout's existing boundary accounts. This invariant is only enforced later in `ShardLayoutV3::derive_impl` during `finalize_epoch`. If `find_trie_split` returns a boundary account that already exists in the shard layout, `finalize_epoch` fails with `EpochError::ShardingError`, causing all nodes to reject the epoch-boundary block and permanently halting the chain.

### Finding Description

The dynamic resharding system enforces the invariant "boundary accounts must be unique" at layout derivation time but not at proposal time. The full data flow is:

**Step 1 — Proposal (no invariant check):**
During chunk application, `compute_proposed_split()` calls `check_dynamic_resharding()`, which calls `find_trie_split()` to compute a `TrieSplit` with a `boundary_account`. `check_dynamic_resharding` does **not** check whether `boundary_account` already exists in `shard_layout.boundary_accounts()`. [1](#0-0) 

**Step 2 — Propagation into block header:**
The `TrieSplit` is stored in `ChunkExtra.proposed_split` and embedded in `ShardChunkHeaderInnerV5.proposed_split`. At epoch boundary, `get_upcoming_shard_split()` picks the winning split and embeds it in `BlockHeaderInnerRestV6.shard_split`. [2](#0-1) 

**Step 3 — Block validation passes (no duplicate check):**
`validate_block_shard_split()` validates that the block header's `shard_split` matches the recomputed value from chunk headers. It does **not** check whether the boundary account is a duplicate. [3](#0-2) 

**Step 4 — Invariant enforced too late, causing `finalize_epoch` failure:**
During `finalize_epoch()`, `next_next_shard_layout()` calls `next_shard_layout.derive_v3(boundary_account, ...)`. `ShardLayoutV3::derive_impl` performs `boundary_accounts.binary_search(&new_boundary_account)` and returns `Err(ShardLayoutError::DuplicateBoundaryAccount)` if the account already exists. [4](#0-3) 

This error propagates through `next_next_shard_layout()` via `map_err`: [5](#0-4) 

And then through `finalize_epoch()` via `?`: [6](#0-5) 

**Step 5 — Chain halt:**
All nodes reject the epoch-boundary block. The block producer re-produces the same block (same chunk headers → same `proposed_split` → same `shard_split`). The chain halts permanently until the data distribution changes or a protocol upgrade is deployed.

The invariant is explicitly enforced at initialization/derivation: [7](#0-6) 

But is entirely absent from the proposal path: [8](#0-7) 

### Impact Explanation

The corrupted protocol value is `EpochInfo.shard_layout` for epoch N+2, which is never written because `finalize_epoch` fails before storing it. This prevents epoch finalization for all nodes simultaneously. Since `find_trie_split` is deterministic (based on the trie

### Citations

**File:** chain/chain/src/runtime/mod.rs (L1727-1765)
```rust
fn check_dynamic_resharding(
    shard_trie: &Trie,
    shard_id: ShardId,
    shard_layout: ShardLayout,
    config: &DynamicReshardingConfig,
) -> Result<Option<TrieSplit>, FindSplitError> {
    let shard_uid = ShardUId::from_shard_id_and_layout(shard_id, &shard_layout);
    let mem_usage = total_mem_usage(shard_trie)?;

    DYNAMIC_RESHARDING_SHARD_MEMORY_USAGE
        .with_label_values(&[&shard_uid.to_string()])
        .set(mem_usage as i64);
    DYNAMIC_RESHARDING_MEMORY_USAGE_THRESHOLD.set(config.memory_usage_threshold as i64);
    DYNAMIC_RESHARDING_MIN_CHILD_MEMORY_USAGE.set(config.min_child_memory_usage as i64);
    DYNAMIC_RESHARDING_MAX_NUMBER_OF_SHARDS.set(config.max_number_of_shards as i64);

    if shard_layout.num_shards() >= config.max_number_of_shards {
        return Ok(None);
    }
    // maximum number of shards takes precedence over force-split – DO NOT REORDER
    if config.force_split_shards.contains(&shard_id) {
        return Ok(Some(find_trie_split(shard_trie)?));
    }
    if config.block_split_shards.contains(&shard_id) {
        return Ok(None);
    }

    if mem_usage < config.memory_usage_threshold {
        return Ok(None);
    }
    let trie_split = find_trie_split(shard_trie)?;
    if trie_split.left_memory < config.min_child_memory_usage {
        return Ok(None);
    }
    if trie_split.right_memory < config.min_child_memory_usage {
        return Ok(None);
    }
    Ok(Some(trie_split))
}
```

**File:** chain/epoch-manager/src/lib.rs (L794-799)
```rust
        let new_layout = next_shard_layout
            .derive_v3(boundary_account.clone(), || {
                self.get_shard_layout_history(current_protocol_version, None)
            })
            .map_err(|err| EpochError::ShardingError(err.to_string()))?;
        Ok(new_layout)
```

**File:** chain/epoch-manager/src/lib.rs (L918-924)
```rust
        let next_next_shard_layout = self.next_next_shard_layout(
            &epoch_config,
            epoch_protocol_version,
            &next_next_epoch_config,
            &next_shard_layout,
            block_info,
        )?;
```

**File:** chain/epoch-manager/src/lib.rs (L2192-2228)
```rust
    pub fn get_upcoming_shard_split(
        &self,
        protocol_version: ProtocolVersion,
        parent_hash: &CryptoHash,
        chunk_headers: &[ShardChunkHeader],
    ) -> Result<Option<(ShardId, AccountId)>, EpochError> {
        // Check if dynamic resharding is enabled
        let epoch_config = self.get_epoch_config(protocol_version);
        let dynamic_resharding_config = match &epoch_config.shard_layout_config {
            ShardLayoutConfig::Static { .. } => return Ok(None),
            ShardLayoutConfig::Dynamic { dynamic_resharding_config } => dynamic_resharding_config,
        };

        // Check if resharding is allowed based on epoch constraints
        let can_reshard = self
            .can_reshard(&parent_hash, dynamic_resharding_config.min_epochs_between_resharding)?;
        if !can_reshard {
            return Ok(None);
        }

        // Collect proposed splits from chunk headers
        let mut proposed_splits = HashMap::new();
        for chunk_header in chunk_headers {
            if let Some(split) = chunk_header.proposed_split() {
                proposed_splits.insert(chunk_header.shard_id(), split.clone());
            }
        }

        // Pick the shard to split
        let Some((shard_id, split)) =
            pick_shard_to_split(&proposed_splits, dynamic_resharding_config)
        else {
            return Ok(None);
        };

        Ok(Some((shard_id, split.boundary_account)))
    }
```

**File:** chain/chain/src/validate.rs (L193-227)
```rust
pub fn validate_block_shard_split(
    epoch_manager: &dyn EpochManagerAdapter,
    header: &BlockHeader,
    chunk_headers: &[ShardChunkHeader],
) -> Result<(), Error> {
    let is_last_block = epoch_manager.is_produced_block_last_in_epoch(
        header.height(),
        header.prev_hash(),
        header.last_final_block(),
    )?;

    let expected_shard_split = if is_last_block {
        let protocol_version = epoch_manager.get_epoch_protocol_version(header.epoch_id())?;
        epoch_manager.get_upcoming_shard_split(
            protocol_version,
            header.prev_hash(),
            chunk_headers,
        )?
    } else {
        None
    };

    let header_shard_split = header.shard_split();
    if header_shard_split != expected_shard_split.as_ref() {
        DYNAMIC_RESHARDING_VALIDATION_FAILURES.with_label_values(&["block_header"]).inc();
        return Err(Error::InvalidBlockHeaderShardSplit(format!(
            "header has {:?}, expected {:?} (block hash: {:?} height: {:?})",
            header_shard_split,
            expected_shard_split,
            header.hash(),
            header.height(),
        )));
    }

    Ok(())
```

**File:** core/primitives/src/shard_layout/v3.rs (L204-213)
```rust
impl ShardLayoutV3 {
    pub fn new(
        boundary_accounts: Vec<AccountId>,
        shard_ids: Vec<ShardId>,
        shards_split_map: ShardsSplitMapV3,
        last_split: ShardId,
    ) -> Self {
        assert_eq!(boundary_accounts.len() + 1, shard_ids.len());
        assert!(boundary_accounts.is_sorted());
        assert!(shards_split_map.get(&last_split).is_some_and(|children| !children.is_empty()));
```

**File:** core/primitives/src/shard_layout/v3.rs (L258-268)
```rust
    fn derive_impl(
        mut shard_ids: Vec<ShardId>,
        mut boundary_accounts: Vec<AccountId>,
        new_boundary_account: AccountId,
        mut shards_split_map: ShardsSplitMapV3,
    ) -> Result<Self, ShardLayoutError> {
        let Err(new_boundary_idx) = boundary_accounts.binary_search(&new_boundary_account) else {
            return Err(ShardLayoutError::DuplicateBoundaryAccount {
                account_id: new_boundary_account,
            });
        };
```
