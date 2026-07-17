## Analysis

Let me trace the exact code path to verify the claim.

### Step 1: `validate_chunk_proofs` — what it checks [1](#0-0) 

`validate_chunk_proofs` checks:
1. `chunk.header_hash() == chunk.compute_header_hash()` — internal hash consistency
2. `tx_root` matches the transactions
3. `receipts_root` matches the outgoing receipts

It does **not** check `chunk.shard_id() == shard_id`.

### Step 2: `verify_path` in `set_state_header` — what it checks [2](#0-1) 

This is a Merkle **membership** proof: it verifies that `ChunkHashHeight(chunk.chunk_hash(), chunk.height_included())` is *somewhere* in the tree rooted at `chunk_headers_root`. It does **not** verify that the chunk is at the index position corresponding to `shard_id`. In a 2-shard block, shard 0's chunk proof (position 0) and shard 1's chunk proof (position 1) are distinct paths, but the check only verifies membership — not which position was proven.

### Step 3: No `chunk.shard_id() == shard_id` guard anywhere in `set_state_header` [3](#0-2) 

Scanning the entire function: there is no assertion that `chunk.shard_id()` equals the `shard_id` parameter. The header is stored under `StateHeaderKey(shard_id, sync_hash)` regardless of what shard the embedded chunk actually belongs to. [4](#0-3) 

### Step 4: `set_state_finalize` uses `shard_id` for `shard_uid` but the chunk's `prev_state_root` for trie access [5](#0-4) 

`shard_uid` is derived from the `shard_id` parameter (Y), but `chunk_header.prev_state_root()` comes from the embedded chunk (shard X's state root). The `apply_chunk` call uses shard Y's `shard_uid` with shard X's `prev_state_root`. For this to succeed, shard X's state root must exist in shard Y's trie namespace — which happens after `apply_state_part(shard_id=Y, state_root=shardX_state_root, shardX_parts)` is called by the syncing node (because it reads the state root from the stored, malicious header).

### Step 5: `apply_chunk_postprocessing` writes `ChunkExtra` under shard Y's `shard_uid` [6](#0-5) 

The resulting `ChunkExtra` (with the wrong state root) is stored under shard Y's `shard_uid`, diverging from every honest node's `ChunkExtra` for shard Y.

---

### Title
Missing `chunk.shard_id() == shard_id` check in `set_state_header` allows cross-shard state confusion — (`chain/chain/src/state_sync/adapter.rs`)

### Summary
`set_state_header` accepts a `ShardStateSyncResponseHeader` whose embedded `ShardChunk` belongs to shard X when called with `shard_id=Y`, because neither `validate_chunk_proofs` nor the `verify_path` Merkle membership check asserts that the chunk's shard identity matches the requested shard. A malicious peer can exploit this to corrupt the syncing node's `ChunkExtra` and state root for shard Y.

### Finding Description
In `set_state_header` (`chain/chain/src/state_sync/adapter.rs`, lines 368–532):

1. `validate_chunk_proofs` only verifies internal hash consistency (chunk hash, tx_root, receipts_root) — no shard_id check.
2. `verify_path(*sync_prev_block_header.chunk_headers_root(), chunk_proof, &ChunkHashHeight(chunk.chunk_hash(), chunk.height_included()))` is a Merkle membership proof. It proves the chunk is *somewhere* in the block's chunk_headers_root, not that it is at the index position for `shard_id=Y`. An attacker can supply shard X's chunk with shard X's valid Merkle proof (position 0), and the check passes even when `shard_id=Y` (position 1).
3. No guard `chunk.shard_id() == shard_id` exists anywhere in the function.
4. The header is stored under `StateHeaderKey(shard_id=Y, sync_hash)`.

Subsequently:
- The syncing node calls `apply_state_part(shard_id=Y, state_root=shardX_prev_state_root, shardX_parts)`, installing shard X's state into shard Y's trie namespace.
- `set_state_finalize(shard_id=Y)` calls `apply_chunk` with `shard_uid=Y` but `prev_state_root=shardX_prev_state_root`, applying shard X's transactions against shard X's state (now in shard Y's namespace), producing a `ChunkExtra` with a state root that no honest node has for shard Y.

### Impact Explanation
The syncing node ends up with a permanently divergent `ChunkExtra.state_root` for shard Y. It cannot produce or validate chunks for shard Y that match honest nodes, effectively ejecting it from consensus participation for that shard. Recovery requires a full re-sync.

### Likelihood Explanation
Any peer the syncing node contacts for state sync can trigger this. State sync header requests are part of normal production peer protocol. The attacker needs no validator or operator privileges — only the ability to respond to a `StateRequestHeader` message. The crafted header requires only publicly available data: shard X's chunk (from the block), shard X's Merkle proof (from the block), valid incoming receipts for shard Y (from honest nodes), and shard X's state root node (from honest nodes).

### Recommendation
Add an explicit shard identity check immediately after extracting the chunk in `set_state_header`:

```rust
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

This should be placed after line 376 (`let chunk = shard_state_header.cloned_chunk();`) and before the `validate_chunk_proofs` call.

### Proof of Concept
1. Build a two-shard `TestEnv`, produce enough blocks to reach a sync hash.
2. Obtain the honest `ShardStateSyncResponseHeader` for shard 0 (including its chunk, chunk_proof, prev_chunk_header, prev_chunk_proof, and state_root_node).
3. Obtain valid incoming receipts proofs for shard 1 from the honest node.
4. Construct a crafted `ShardStateSyncResponseHeaderV2` with shard 0's chunk/chunk_proof/prev_chunk_header/prev_chunk_proof/state_root_node but shard 1's incoming_receipts_proofs/root_proofs.
5. Call `set_state_header(shard_id=1, sync_hash, crafted_header)` on the syncing node — assert it returns `Ok(())`.
6. Call `apply_state_part(shard_id=1, state_root=shard0_prev_state_root, shard0_parts, ...)`.
7. Call `set_state_finalize(shard_id=1, sync_hash)`.
8. Compare the resulting `ChunkExtra` state_root for shard 1 against the honest node's `ChunkExtra` for shard 1 — they will differ.

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

**File:** chain/chain/src/state_sync/adapter.rs (L368-532)
```rust
    pub fn set_state_header(
        &self,
        shard_id: ShardId,
        sync_hash: CryptoHash,
        shard_state_header: ShardStateSyncResponseHeader,
    ) -> Result<(), Error> {
        let sync_block_header = self.chain_store.get_block_header(&sync_hash)?;

        let chunk = shard_state_header.cloned_chunk();
        let prev_chunk_header = shard_state_header.cloned_prev_chunk_header();

        // 1-2. Checking chunk validity
        if !validate_chunk_proofs(&chunk, self.epoch_manager.as_ref())? {
            byzantine_assert!(false);
            return Err(Error::Other(
                "set_shard_state failed: chunk header proofs are invalid".into(),
            ));
        }

        // Consider chunk itself is valid.

        // 3. Checking that chunks `chunk` and `prev_chunk` are included in appropriate blocks
        // 3a. Checking that chunk `chunk` is included into block at last height before sync_hash
        // 3aa. Also checking chunk.height_included
        let sync_prev_block_header =
            self.chain_store.get_block_header(sync_block_header.prev_hash())?;
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

        let block_header = get_block_header_on_chain_by_height(
            &self.chain_store,
            &sync_hash,
            chunk.height_included(),
        )?;
        // 3b. Checking that chunk `prev_chunk` is included into block at height before chunk.height_included
        // 3ba. Also checking prev_chunk.height_included - it's important for getting correct incoming receipts
        match (&prev_chunk_header, shard_state_header.prev_chunk_proof()) {
            (Some(prev_chunk_header), Some(prev_chunk_proof)) => {
                let prev_block_header =
                    self.chain_store.get_block_header(block_header.prev_hash())?;
                if !verify_path(
                    *prev_block_header.chunk_headers_root(),
                    prev_chunk_proof,
                    &ChunkHashHeight(prev_chunk_header.chunk_hash().clone(), prev_chunk_header.height_included()),
                ) {
                    byzantine_assert!(false);
                    return Err(Error::Other(
                        "set_shard_state failed: prev_chunk isn't included into block".into(),
                    ));
                }
            }
            (None, None) => {
                if chunk.height_included() != 0 {
                    return Err(Error::Other(
                    "set_shard_state failed: received empty state response for a chunk that is not at height 0".into()
                ));
                }
            }
            _ =>
                return Err(Error::Other("set_shard_state failed: `prev_chunk_header` and `prev_chunk_proof` must either both be present or both absent".into()))
        };

        // 4. Proving incoming receipts validity
        // 4a. Checking len of proofs
        if shard_state_header.root_proofs().len()
            != shard_state_header.incoming_receipts_proofs().len()
        {
            byzantine_assert!(false);
            return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
        }
        let mut hash_to_compare = sync_hash;
        for (i, receipt_response) in
            shard_state_header.incoming_receipts_proofs().iter().enumerate()
        {
            let ReceiptProofResponse(block_hash, receipt_proofs) = receipt_response;

            // 4b. Checking that there is a valid sequence of continuous blocks
            if *block_hash != hash_to_compare {
                byzantine_assert!(false);
                return Err(Error::Other(
                    "set_shard_state failed: invalid incoming receipts".into(),
                ));
            }
            let header = self.chain_store.get_block_header(&hash_to_compare)?;
            hash_to_compare = *header.prev_hash();

            let block_header = self.chain_store.get_block_header(block_hash)?;
            // 4c. Checking len of receipt_proofs for current block
            if receipt_proofs.len() != shard_state_header.root_proofs()[i].len()
                || receipt_proofs.len() != block_header.chunks_included() as usize
            {
                byzantine_assert!(false);
                return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
            }
            // We know there were exactly `block_header.chunks_included` chunks included
            // on the height of block `block_hash`.
            // There were no other proofs except for included chunks.
            // According to Pigeonhole principle, it's enough to ensure all receipt_proofs are distinct
            // to prove that all receipts were received and no receipts were hidden.
            let mut visited_shard_ids = HashSet::<ShardId>::new();
            for (j, receipt_proof) in receipt_proofs.iter().enumerate() {
                let ReceiptProof(receipts, shard_proof) = receipt_proof;
                let ShardProof { from_shard_id, to_shard_id: _, proof } = shard_proof;
                // 4d. Checking uniqueness for set of `from_shard_id`
                match visited_shard_ids.get(from_shard_id) {
                    Some(_) => {
                        byzantine_assert!(false);
                        return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
                    }
                    _ => visited_shard_ids.insert(*from_shard_id),
                };
                let RootProof(root, block_proof) = &shard_state_header.root_proofs()[i][j];
                let receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts));
                // 4e. Proving the set of receipts is the subset of outgoing_receipts of shard `shard_id`
                if !verify_path(*root, proof, &receipts_hash) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
                }
                // 4f. Proving the outgoing_receipts_root matches that in the block
                if !verify_path(
                    *block_header.prev_chunk_outgoing_receipts_root(),
                    block_proof,
                    root,
                ) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
                }
            }
        }
        // 4g. Checking that there are no more heights to get incoming_receipts
        let header = self.chain_store.get_block_header(&hash_to_compare)?;
        if header.height() != prev_chunk_header.map_or(0, |h| h.height_included()) {
            byzantine_assert!(false);
            return Err(Error::Other("set_shard_state failed: invalid incoming receipts".into()));
        }

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

        // Saving the header data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();

        Ok(())
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

**File:** chain/chain/src/spice/chunk_application.rs (L56-83)
```rust
pub fn apply_chunk_postprocessing(
    store_update: &mut StoreUpdate,
    runtime_adapter: &dyn RuntimeAdapter,
    block: &Block,
    result: NewChunkResult,
    config: &ChunkPersistenceConfig,
) -> Result<(), Error> {
    let block_hash = block.hash();
    let prev_hash = block.header().prev_hash();
    let height = block.header().height();
    let NewChunkResult { gas_limit, shard_uid, apply_result } = result;
    let shard_id = shard_uid.shard_id();

    let (_, outcome_paths) = ApplyChunkResult::compute_outcomes_proof(&apply_result.outcomes);
    let chunk_extra = apply_result.to_chunk_extra(gas_limit);

    let ApplyChunkResult {
        mut trie_changes,
        outcomes,
        outgoing_receipts,
        processed_receipts,
        receipt_to_tx,
        stats,
        ..
    } = apply_result;

    // `ChunkExtra` marks this shard's apply as done; must share `store_update` with the refcounted writes below.
    store_update.chunk_store_update().set_chunk_extra(block_hash, &shard_uid, &chunk_extra);
```
