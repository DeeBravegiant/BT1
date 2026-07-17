Let me look at the key validation details more carefully.

### Title
Missing `chunk.shard_id() == shard_id` Guard in `set_state_header` Allows Cross-Shard Trie Corruption — (`chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`set_state_header` accepts a `ShardStateSyncResponseHeader` from a peer for a requested `shard_id` but never checks that the embedded chunk's own `shard_id` field matches the requested shard. A malicious peer can substitute a real chunk from shard X (with a valid Merkle inclusion proof for shard X's position) into a response for shard Y. All existing guards pass. The victim then downloads and applies state parts valid against shard X's state root, but `apply_state_part` writes those trie nodes under shard Y's `ShardUId` key prefix, permanently corrupting shard Y's trie on the syncing node.

---

### Finding Description

**Entry point**: `set_state_header` in `chain/chain/src/state_sync/adapter.rs`.

The function accepts a peer-supplied `ShardStateSyncResponseHeader` and runs five validation steps before storing it. None of them check `chunk.shard_id() == shard_id`:

**Step 1 — `validate_chunk_proofs`** verifies the chunk's internal hash consistency and that the tx/receipts roots match the body. It never inspects `chunk.shard_id()`. [1](#0-0) 

**Step 2 — Merkle inclusion proof** verifies that `ChunkHashHeight(chunk.chunk_hash(), chunk.height_included())` is in `sync_prev_block_header.chunk_headers_root()`. The `verify_path` function is position-agnostic: it only checks that the supplied path leads from the item hash to the root. Providing shard X's chunk with shard X's valid Merkle path passes this check even when the request is for shard Y. [2](#0-1) 

**Step 3 — Receipt proofs** are verified using the caller-supplied `shard_id` (the requested shard Y), not `chunk.shard_id()`. An attacker can supply shard Y's real on-chain incoming receipt proofs, which are public data any synced node possesses. [3](#0-2) 

**Step 4 — State root node** is validated against `chunk_inner.prev_state_root()`, which is shard X's state root. Providing shard X's real state root node passes this check. [4](#0-3) 

**Storage**: The header is stored under key `StateHeaderKey(shard_id, sync_hash)` — keyed by the requested shard Y, but containing shard X's chunk. [5](#0-4) 

**Downstream in `run_state_sync_for_shard`**: `state_root` is taken from the stored header's chunk (shard X's `prev_state_root`), while `shard_uid` is derived from the requested `shard_id` (shard Y). [6](#0-5) 

**`validate_state_part_impl`** only checks the part against `state_root` — it ignores `shard_id` entirely (used only for metrics). State parts valid against shard X's state root pass validation when `shard_id=Y` is passed. [7](#0-6) 

**`apply_state_part`** resolves `shard_uid` from the requested `shard_id` (Y), then writes all trie changes and flat state delta under shard Y's key prefix — even though the trie nodes belong to shard X. [8](#0-7) 

---

### Impact Explanation

The syncing node's shard Y trie is populated with shard X's trie nodes under shard Y's `ShardUId` key prefix. Shard Y's actual state is never initialized. After `set_state_finalize`, the node operates with a permanently corrupted view of shard Y: account balances, nonces, contract storage, and receipts for shard Y are all wrong. If the node is a validator, it will produce invalid chunks for shard Y or fail to validate legitimate ones, with direct chain safety consequences.

---

### Likelihood Explanation

The attacker only needs to be a reachable peer during state sync — no validator key, stake, or privileged access is required. All the data needed to craft the malicious header (shard X's chunk, its Merkle path, shard Y's receipt proofs, shard X's state root node, shard X's state parts) is public on-chain data available to any fully-synced node. The attack is executable by any node operator willing to serve crafted state sync responses.

---

### Recommendation

Add an explicit shard identity check at the top of `set_state_header`, immediately after extracting the chunk:

```rust
if chunk.shard_id() != shard_id {
    return Err(Error::Other(format!(
        "set_shard_state failed: chunk shard_id {} does not match requested shard_id {}",
        chunk.shard_id(), shard_id
    )));
}
```

This check should be placed before any other validation, at approximately line 376 of `chain/chain/src/state_sync/adapter.rs`. [9](#0-8) 

---

### Proof of Concept

A unit test can construct a `ShardStateSyncResponseHeaderV2` where `chunk` is the real chunk for shard 0 (with its valid Merkle proof for shard 0's position in the block), `incoming_receipts_proofs` are shard 1's real incoming receipts, and `state_root_node` is shard 0's real state root node. Calling `set_state_header(shard_id=1, sync_hash, crafted_header)` will return `Ok(())` with current code. Subsequently calling `apply_state_part` for shard 1 will write shard 0's trie nodes under shard 1's `ShardUId` prefix, which can be verified by inspecting the DB keys written.

### Citations

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

**File:** chain/chain/src/state_sync/adapter.rs (L376-385)
```rust
        let chunk = shard_state_header.cloned_chunk();
        let prev_chunk_header = shard_state_header.cloned_prev_chunk_header();

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

**File:** chain/chain/src/state_sync/adapter.rs (L487-492)
```rust
                let RootProof(root, block_proof) = &shard_state_header.root_proofs()[i][j];
                let receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts));
                // 4e. Proving the set of receipts is the subset of outgoing_receipts of shard `shard_id`
                if !verify_path(*root, proof, &receipts_hash) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
```

**File:** chain/chain/src/state_sync/adapter.rs (L512-523)
```rust
        // 5. Checking that state_root_node is valid
        let chunk_inner = chunk.take_header().take_inner();
        if matches!(
            self.runtime_adapter.validate_state_root_node(
                shard_state_header.state_root_node(),
                chunk_inner.prev_state_root(),
            ),
            StateRootNodeValidationResult::Invalid
        ) {
            byzantine_assert!(false);
            return Err(Error::Other("set_shard_state failed: state_root_node is invalid".into()));
        }
```

**File:** chain/chain/src/state_sync/adapter.rs (L526-529)
```rust
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();
```

**File:** chain/client/src/sync/state/shard.rs (L75-83)
```rust
    let header = downloader.ensure_shard_header(shard_id, sync_hash, cancel.clone()).await?;
    let state_root = header.chunk_prev_state_root();
    let num_parts = header.num_state_parts();
    let block_header =
        store.get_ser::<BlockHeader>(DBCol::BlockHeader, sync_hash.as_bytes()).ok_or_else(
            || near_chain::Error::DBNotFoundErr(format!("No block header {}", sync_hash)),
        )?;
    let epoch_id = *block_header.epoch_id();
    let shard_uid = shard_id_to_uid(epoch_manager.as_ref(), shard_id, &epoch_id)?;
```

**File:** chain/chain/src/runtime/mod.rs (L1481-1499)
```rust
    fn validate_state_part(
        &self,
        shard_id: ShardId,
        state_root: &StateRoot,
        part_id: PartId,
        part: &StatePart,
    ) -> StatePartValidationResult {
        let instant = Instant::now();
        let res = self.validate_state_part_impl(state_root, part_id, part);
        let elapsed = instant.elapsed();
        let is_ok = match res {
            StatePartValidationResult::Valid => "ok",
            StatePartValidationResult::Invalid => "error",
        };
        metrics::STATE_SYNC_VALIDATE_PART_DELAY
            .with_label_values(&[shard_id.to_string().as_str(), is_ok])
            .observe(elapsed.as_secs_f64());
        res
    }
```

**File:** chain/chain/src/runtime/mod.rs (L1519-1525)
```rust
        let shard_uid = self.get_shard_uid_from_epoch_id(shard_id, epoch_id)?;
        let mut store_update = tries.store_update();
        tries.apply_all(&trie_changes, shard_uid, &mut store_update);
        tracing::debug!(target: "chain", %shard_id, values_count = %flat_state_delta.len(), "inserting values to flat storage");
        // TODO: `apply_to_flat_state` inserts values with random writes, which can be time consuming.
        //       Optimize taking into account that flat state values always correspond to a consecutive range of keys.
        flat_state_delta.apply_to_flat_state(&mut store_update.flat_store_update(), shard_uid);
```
