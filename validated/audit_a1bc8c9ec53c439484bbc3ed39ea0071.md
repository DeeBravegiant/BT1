The code trace is complete. Let me summarize the findings across the key functions.

**Trace of the attack path:**

**Step 1 — `set_state_header` validation (what IS checked):**

In `set_state_header`, for each `ReceiptProofResponse`, the inner `receipt_proofs` vector is validated as follows:
- **4b**: Block hash sequence is enforced (outer ordering of `ReceiptProofResponse` entries is fixed)
- **4c**: `receipt_proofs.len() == block_header.chunks_included()` — count must match
- **4d**: `from_shard_id` uniqueness — no duplicate source shards (Pigeonhole argument)
- **4e**: `verify_path(*root, proof, &receipts_hash)` — per-proof Merkle check
- **4f**: `verify_path(*block_header.prev_chunk_outgoing_receipts_root(), block_proof, root)` — per-root Merkle check [1](#0-0) 

**What is NOT checked**: The ordering of `ReceiptProof` entries within a `ReceiptProofResponse`. The `root_proofs[i][j]` is matched with `receipt_proofs[j]` by index — so a malicious peer can swap entries at positions 0 and 1 in `receipt_proofs` and correspondingly swap `root_proofs[i][0]` and `root_proofs[i][1]`. All per-proof Merkle checks still pass, uniqueness still holds, count still matches.

**Step 2 — `set_state_finalize` uses receipts in peer-supplied order, without re-shuffling:** [2](#0-1) 

`collect_receipts_from_response` iterates `ReceiptProofResponse` → `ReceiptProof` → `Receipt` in the order they appear in the header: [3](#0-2) 

There is **no call to `shuffle_receipt_proofs`** anywhere in `set_state_finalize`. Compare with normal block processing: [4](#0-3) 

And stateless validation: [5](#0-4) 

**Step 3 — Receipt order is state-transition-sensitive:**

The runtime processes incoming receipts in the order of the `&receipts` slice passed to `apply_chunk`. If gas runs out mid-processing, remaining receipts are enqueued as delayed receipts in that order. Different orderings produce different delayed receipt queues → different trie state → different state root in `ChunkExtra`. [6](#0-5) 

**Step 4 — Honest nodes store receipts in shuffled order:**

During normal block processing, `collect_incoming_receipts_from_chunks` shuffles receipt proofs using `shuffle_receipt_proofs(receipt_proofs, shuffle_salt)` where `shuffle_salt = block.header().prev_hash()` (a deterministic, block-committed value). These shuffled proofs are then stored via `save_incoming_receipt`. So an honest `ShardStateSyncResponseHeader` carries receipts in the shuffled canonical order. [7](#0-6) 

**Conclusion:**

A malicious peer can construct a `ShardStateSyncResponseHeaderV2` with `incoming_receipts_proofs[i].1` (the inner `ReceiptProof` vector) in a permuted order, with `root_proofs[i]` permuted identically. All of steps 4b–4g pass. `set_state_finalize` then calls `apply_chunk` with receipts in the malicious order (not the canonical shuffled order), producing a `ChunkExtra` with a state root that diverges from what honest nodes computed for the same block.

---

### Title
Receipt proof ordering not enforced in `set_state_header`, causing state root divergence in `set_state_finalize` — (`chain/chain/src/state_sync/adapter.rs`, `chain/chain/src/chain_update.rs`)

### Summary
`set_state_header` validates each `ReceiptProof` entry individually (Merkle path, uniqueness of `from_shard_id`, count) but does not enforce the ordering of `ReceiptProof` entries within a `ReceiptProofResponse`. `set_state_finalize` consumes the receipts in peer-supplied order without re-applying the canonical `shuffle_receipt_proofs`. A malicious state-sync peer can serve a permuted header that passes all validation, causing the syncing node to apply receipts in a non-canonical order and produce a divergent `ChunkExtra` state root.

### Finding Description
In `set_state_header` (`chain/chain/src/state_sync/adapter.rs`), the inner loop at step 4d–4f verifies each `ReceiptProof` at index `j` against `root_proofs[i][j]` by index. The check only enforces that `from_shard_id` values are distinct and that the count equals `block_header.chunks_included()`. It does not enforce any canonical ordering of the `ReceiptProof` entries within the inner vector.

A malicious peer can take a valid header, swap two `ReceiptProof` entries (e.g., positions 0 and 1) in `incoming_receipts_proofs[i].1`, and correspondingly swap `root_proofs[i][0]` and `root_proofs[i][1]`. Every per-proof Merkle check still passes because each proof is still matched with its correct root. The uniqueness check still passes. The count check still passes.

In `set_state_finalize` (`chain/chain/src/chain_update.rs`), `collect_receipts_from_response` flattens the `ReceiptProof` entries in the order they appear in the header. There is no call to `shuffle_receipt_proofs` before passing `&receipts` to `apply_chunk`. Honest nodes, by contrast, store receipts in the shuffled canonical order (keyed by `block.header().prev_hash()`), so an honest header carries the canonical order. The malicious header carries a different order, and `apply_chunk` processes receipts in that different order.

### Impact Explanation
Receipt processing order is state-transition-sensitive. If gas runs out mid-processing, remaining receipts are enqueued as delayed receipts in the order they were encountered. Different orderings produce different delayed receipt queues, different trie writes, and a different state root in `ChunkExtra`. The syncing node's `ChunkExtra` will have a state root that diverges from the canonical chain. Subsequent chunk applications on that node will use the wrong `prev_state_root`, causing cascading divergence. The node cannot participate correctly in consensus or serve correct RPC results after this point.

### Likelihood Explanation
State sync is a standard production code path used by any node catching up after downtime or initial sync. The attacker only needs to be a peer that the syncing node selects for state sync — no validator or operator privileges are required. Running a malicious NEAR peer node is within reach of an unprivileged external actor.

### Recommendation
In `set_state_finalize`, after collecting `receipt_proof_responses`, apply `shuffle_receipt_proofs` per block (using `get_receipts_shuffle_salt`) before calling `collect_receipts_from_response`, mirroring what `collect_incoming_receipts_from_chunks` does during normal block processing. Alternatively, enforce canonical ordering in `set_state_header` by checking that `receipt_proofs` within each `ReceiptProofResponse` are sorted by `from_shard_id` (matching the order in which honest nodes store them).

### Proof of Concept
1. Obtain a valid `ShardStateSyncResponseHeaderV2` for a block where `incoming_receipts_proofs[0].1` has at least two entries (i.e., at least two source shards sent receipts).
2. Construct a second header identical to the first except: swap `incoming_receipts_proofs[0].1[0]` and `incoming_receipts_proofs[0].1[1]`, and correspondingly swap `root_proofs[0][0]` and `root_proofs[0][1]`.
3. Feed both headers through `set_state_header` — both return `Ok(())`.
4. Feed both headers through `set_state_finalize` — assert the resulting `ChunkExtra` state roots are equal. The assertion fails, demonstrating divergence.

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L475-503)
```rust
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
```

**File:** chain/chain/src/chain_update.rs (L478-487)
```rust
        // Getting actual incoming receipts.
        let mut receipt_proof_responses: Vec<ReceiptProofResponse> = vec![];
        for incoming_receipt_proof in &incoming_receipts_proofs {
            let ReceiptProofResponse(hash, _) = incoming_receipt_proof;
            let block_header = self.chain_store_update.get_block_header(hash)?;
            if block_header.height() <= chunk.height_included() {
                receipt_proof_responses.push(incoming_receipt_proof.clone());
            }
        }
        let receipts = collect_receipts_from_response(&receipt_proof_responses);
```

**File:** chain/chain/src/chain_update.rs (L519-542)
```rust
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

**File:** chain/chain/src/chain.rs (L1200-1203)
```rust
        // sort the receipts deterministically so the order that they will be processed is deterministic
        for (_, receipt_proofs) in &mut receipt_proofs_by_shard_id {
            shuffle_receipt_proofs(receipt_proofs, shuffle_salt);
        }
```

**File:** chain/chain/src/chain.rs (L4134-4147)
```rust
pub fn collect_receipts<'a, T>(receipt_proofs: T) -> Vec<Receipt>
where
    T: IntoIterator<Item = &'a ReceiptProof>,
{
    receipt_proofs.into_iter().flat_map(|ReceiptProof(receipts, _)| receipts).cloned().collect()
}

pub fn collect_receipts_from_response(
    receipt_proof_response: &[ReceiptProofResponse],
) -> Vec<Receipt> {
    collect_receipts(
        receipt_proof_response.iter().flat_map(|ReceiptProofResponse(_, proofs)| proofs.iter()),
    )
}
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L507-511)
```rust
        // Arrange the receipts in the order in which they should be applied.
        shuffle_receipt_proofs(&mut block_receipt_proofs, get_receipts_shuffle_salt(block));
        for proof in block_receipt_proofs {
            receipts_to_apply.extend(proof.0.iter().cloned());
        }
```

**File:** chain/chain/src/sharding.rs (L13-21)
```rust
pub fn shuffle_receipt_proofs<ReceiptProofType>(
    receipt_proofs: &mut Vec<ReceiptProofType>,
    shuffle_salt: &CryptoHash,
) {
    let mut slice = [0u8; 32];
    slice.copy_from_slice(shuffle_salt.as_ref());
    let mut rng: ChaCha20Rng = SeedableRng::from_seed(slice);
    receipt_proofs.shuffle(&mut rng);
}
```
