Now I have enough information to make a definitive assessment. Let me trace the exact validation logic.

The grep confirms there is **no** `chunk.shard_id() == shard_id` comparison anywhere in `set_state_header`, and `verify_path_with_index` (which checks position) is never used there — only the position-blind `verify_path` is called.

---

### Title
Missing shard-identity check in `set_state_header` allows a malicious peer to install a cross-shard state header, corrupting the syncing node's state root — (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header` validates that a received `ShardStateSyncResponseHeader`'s chunk is *somewhere* in the block's chunk Merkle tree, but never verifies that the chunk occupies the position that corresponds to the requested `shard_id`. Because `verify_path` is position-blind and `validate_chunk_proofs` only checks internal chunk consistency, a malicious peer can supply shard 0's chunk (with its valid position-0 Merkle proof) in response to a shard-1 state-sync request. The header passes every guard and is persisted under `StateHeaderKey(shard_id=1, sync_hash)`. Downstream, `set_state_finalize` applies shard 0's transactions and receipts against shard 1's `shard_uid`, producing a wrong `state_root` for shard 1 on the syncing node.

### Finding Description

**Root cause — no shard identity assertion.**

`set_state_header` extracts the chunk from the peer-supplied header and runs two checks:

1. `validate_chunk_proofs(&chunk, ...)` — verifies the chunk's internal hash, tx-root, and receipts-root consistency. It never compares `chunk.shard_id()` to the `shard_id` parameter. [1](#0-0) [2](#0-1) 

2. `verify_path(chunk_headers_root, chunk_proof, ChunkHashHeight(...))` — verifies the chunk hash is reachable from the block's Merkle root via the supplied path. This is the position-blind variant; it does **not** verify which leaf index the path encodes. [3](#0-2) [4](#0-3) 

The position-aware variant `verify_path_with_index` exists and is used elsewhere (e.g., chunk-part validation), but is absent here. [5](#0-4) 

After both checks pass, the header is stored keyed by the *caller-supplied* `shard_id`, not by the chunk's actual shard: [6](#0-5) 

**Receipt proof check does not close the gap.**

Step 4e computes `CryptoHash::hash_borsh(ReceiptList(shard_id, receipts))` using the *parameter* `shard_id=1`. [7](#0-6) 

An attacker can satisfy this by supplying shard 1's actual incoming receipts (publicly observable on-chain) while keeping shard 0's chunk. The `root` and `block_proof` fields in `RootProof` are attacker-controlled and can be set to the real chain values for shard 1's outgoing-receipts Merkle tree, which are also publicly observable.

**Crafted header that passes every guard:**

| Field | Value |
|---|---|
| `chunk` | Shard 0's chunk (valid internal proofs) |
| `chunk_proof` | Shard 0's valid position-0 Merkle path |
| `prev_chunk_header` | Shard 0's prev chunk header (valid position-0 path) |
| `incoming_receipts_proofs` | Shard 1's real incoming receipts (from chain) |
| `root_proofs` | Real `prev_outgoing_receipts_root` values for shard 1 |
| `state_root_node` | Shard 0's real state root node |

In a typical network where all shards produce a chunk every block, shard 0 and shard 1 share the same `height_included`, so the step-4g height-range check also passes. [8](#0-7) 

### Impact Explanation

`set_state_finalize` reads the stored header back under `StateHeaderKey(shard_id=1, sync_hash)`, extracts shard 0's chunk, and calls `apply_chunk` with `shard_uid` derived from `shard_id=1` but `prev_state_root` from shard 0's chunk header. [9](#0-8) 

The runtime applies shard 0's transactions and receipts to shard 1's trie rooted at shard 0's state root (which was installed via the attacker-supplied state parts). The resulting `apply_result` is committed as shard 1's new state, producing a `state_root` for shard 1 that diverges from every honest node. The syncing node is permanently on a forked state for shard 1.

### Likelihood Explanation

Any unprivileged node that can appear in the syncing node's peer list can trigger this. The syncing node downloads the header from whichever peer responds first. All data needed to craft the header (chunk bodies, Merkle paths, receipt proofs, state root nodes) is publicly observable from the chain. No validator key, stake, or privileged access is required.

### Recommendation

Add an explicit shard-identity assertion immediately after extracting the chunk in `set_state_header`:

```rust
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

Additionally, replace the position-blind `verify_path` call with `verify_path_with_index`, supplying the shard index derived from `shard_id` and the total number of shards, so the Merkle proof is bound to the correct leaf position.

### Proof of Concept

In a two-shard test environment:

1. Obtain a valid `ShardStateSyncResponseHeader` for shard 0 at `sync_hash`.
2. Replace its `incoming_receipts_proofs` and `root_proofs` with the real values for shard 1 (fetched from an honest node).
3. Call `set_state_header(shard_id=1, sync_hash, crafted_header)` on the victim node.
4. Assert the call returns `Ok(())` — it will, because no check compares `chunk.shard_id()` to `shard_id=1`.
5. Supply shard 0's state parts via `set_state_part(shard_id=1, ...)`.
6. Call `set_state_finalize(shard_id=1, sync_hash)`.
7. Read back the committed `state_root` for shard 1 and compare it to an honest node's shard-1 `state_root` — they will differ, confirming the invariant violation.

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L379-385)
```rust
        // 1-2. Checking chunk validity
        if !validate_chunk_proofs(&chunk, self.epoch_manager.as_ref())? {
            byzantine_assert!(false);
            return Err(Error::Other(
                "set_shard_state failed: chunk header proofs are invalid".into(),
            ));
        }
```

**File:** chain/chain/src/state_sync/adapter.rs (L394-403)
```rust
        if !verify_path(
            *sync_prev_block_header.chunk_headers_root(),
            shard_state_header.chunk_proof(),
            &ChunkHashHeight(chunk.chunk_hash().clone(), chunk.height_included()),
        ) {
            byzantine_assert!(false);
            return Err(Error::Other(
                "set_shard_state failed: chunk isn't included into block".into(),
            ));
        }
```

**File:** chain/chain/src/state_sync/adapter.rs (L488-492)
```rust
                let receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts));
                // 4e. Proving the set of receipts is the subset of outgoing_receipts of shard `shard_id`
                if !verify_path(*root, proof, &receipts_hash) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
```

**File:** chain/chain/src/state_sync/adapter.rs (L505-510)
```rust
        // 4g. Checking that there are no more heights to get incoming_receipts
        let header = self.chain_store.get_block_header(&hash_to_compare)?;
        if header.height() != prev_chunk_header.map_or(0, |h| h.height_included()) {
            byzantine_assert!(false);
            return Err(Error::Other("set_shard_state failed: invalid incoming receipts".into()));
        }
```

**File:** chain/chain/src/state_sync/adapter.rs (L526-529)
```rust
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();
```

**File:** chain/chain/src/validate.rs (L22-66)
```rust
pub fn validate_chunk_proofs(
    chunk: &ShardChunk,
    epoch_manager: &dyn EpochManagerAdapter,
) -> Result<bool, Error> {
    let correct_chunk_hash = chunk.compute_header_hash();

    // 1. Checking chunk.header.hash
    let header_hash = chunk.header_hash();
    if header_hash != &correct_chunk_hash {
        byzantine_assert!(false);
        return Ok(false);
    }

    // 2. Checking that chunk body is valid
    // 2a. Checking chunk hash
    if chunk.chunk_hash() != &correct_chunk_hash {
        byzantine_assert!(false);
        return Ok(false);
    }
    let height_created = chunk.height_created();
    let outgoing_receipts_root = chunk.prev_outgoing_receipts_root();
    let (transactions, receipts) = (chunk.to_transactions(), chunk.prev_outgoing_receipts());

    // 2b. Checking that chunk transactions are valid
    let (tx_root, _) = merklize(transactions);
    if &tx_root != chunk.tx_root() {
        byzantine_assert!(false);
        return Ok(false);
    }
    // 2c. Checking that chunk receipts are valid
    if height_created == 0 {
        return Ok(receipts.is_empty() && outgoing_receipts_root == &CryptoHash::default());
    } else {
        let shard_layout = {
            let prev_block_hash = chunk.prev_block_hash();
            epoch_manager.get_shard_layout_from_prev_block(&prev_block_hash)?
        };
        let outgoing_receipts_hashes = Chain::build_receipts_hashes(receipts, &shard_layout)?;
        let (receipts_root, _) = merklize(&outgoing_receipts_hashes);
        if &receipts_root != outgoing_receipts_root {
            byzantine_assert!(false);
            return Ok(false);
        }
    }
    Ok(true)
```

**File:** core/primitives/src/merkle.rs (L113-119)
```rust
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}

pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
}
```

**File:** core/primitives/src/merkle.rs (L121-129)
```rust
pub fn verify_path_with_index<T: BorshSerialize>(
    root: MerkleHash,
    path: &MerklePath,
    item: T,
    part_idx: u64,
    num_merklized_parts: u64,
) -> bool {
    verify_path_matches_index(path, part_idx, num_merklized_parts) && verify_path(root, path, item)
}
```

**File:** chain/chain/src/chain_update.rs (L513-542)
```rust
        let shard_uid =
            shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
        let memtrie_pin = self
            .runtime_adapter
            .get_tries()
            .maybe_pin_memtrie_root(shard_uid, chunk_header.prev_state_root())?;
        let apply_result = self.runtime_adapter.apply_chunk(
            RuntimeStorageConfig::new(chunk_header.prev_state_root(), true),
            ApplyChunkReason::UpdateTrackedShard,
            ApplyChunkShardContext {
                shard_uid,
                gas_limit,
                last_validator_proposals: chunk_header.prev_validator_proposals(),
                is_new_chunk: true,
                on_post_state_ready: None,
                memtrie_pin,
            },
            ApplyChunkBlockContext {
                block_type: BlockType::Normal,
                height: chunk_header.height_included(),
                prev_block_hash: *chunk_header.prev_block_hash(),
                block_timestamp: block_header.raw_timestamp(),
                gas_price,
                random_seed: *block_header.random_value(),
                congestion_info: block.block_congestion_info(),
                bandwidth_requests: block.block_bandwidth_requests(),
            },
            &receipts,
            transactions,
        )?;
```
