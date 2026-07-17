### Title
Stale Shard Index Used to Build `outcome_root_proof` After Resharding — (`File: chain/client/src/view_client_actor.rs`)

### Summary
`GetExecutionOutcome` computes `target_shard_index` from the epoch of the outcome block (old shard layout), then uses that index to select an entry from `outcome_roots` collected from a later block that may belong to a new epoch with a different shard layout. After a resharding event the index is stale, causing the handler to return an `outcome_root_proof` for the wrong shard's outcome root.

### Finding Description
In `view_client_actor.rs`, the `GetExecutionOutcome` handler:

1. Reads `epoch_id` and `shard_layout` from the block where the outcome was recorded (`outcome_proof.block_hash`).
2. Computes `target_shard_index = shard_layout.get_shard_index(target_shard_id)` — an index into the **old** epoch's shard layout.
3. Calls `get_next_block_hash_with_new_chunk`, which correctly walks forward and, when crossing an epoch boundary with a shard layout change, replaces `target_shard_id` with the appropriate child shard ID in the new layout and returns a block `h` in the **new** epoch.
4. Collects `outcome_roots` from block `h` — one entry per shard in the **new** layout.
5. Returns `merklize(&outcome_roots).1[target_shard_index]` as the `outcome_root_proof`.

`target_shard_index` was derived from the old layout and is never recomputed against the new layout. After a shard split (e.g., shard 2 → shards 2 and 4), the child shard 4 sits at index 4 in the new layout, but `target_shard_index` still holds 2. The proof returned is for shard 2's outcome root, not shard 4's.

```
// view_client_actor.rs ~line 1148
let epoch_id = *self.chain.get_block(&outcome_proof.block_hash)?.header().epoch_id();
let shard_layout = self.epoch_manager.get_shard_layout(&epoch_id)...;
let target_shard_id = account_id_to_shard_id(..., &epoch_id)...;
let target_shard_index = shard_layout.get_shard_index(target_shard_id)...;  // OLD layout index

let res = self.chain.get_next_block_hash_with_new_chunk(
    &outcome_proof.block_hash, target_shard_id)?;

if let Some((h, target_shard_id)) = res {   // target_shard_id updated, target_shard_index NOT
    outcome_proof.block_hash = h;
    let outcome_roots = self.chain.get_block(&h)?.chunks().iter()
        .map(|header| *header.prev_outcome_root())
        .collect::<Vec<_>>();               // NEW layout, more entries
    // bounds check passes but index is wrong
    Ok(GetExecutionOutcomeResponse {
        outcome_proof: outcome_proof.into(),
        outcome_root_proof: merklize(&outcome_roots).1[target_shard_index].clone(), // WRONG shard
    })
}
``` [1](#0-0) 

`get_next_block_hash_with_new_chunk` correctly updates `shard_ids` when crossing epoch boundaries: [2](#0-1) 

But the caller never recomputes `target_shard_index` against the new layout after the call returns.

### Impact Explanation
The `outcome_root_proof` field of `GetExecutionOutcomeResponse` is a Merkle path proving that the target shard's outcome root is committed to in block `h`. When `target_shard_index` is stale, the proof is for a different shard's outcome root. Any light client or verifier that uses this proof to authenticate the execution outcome will fail verification (or, if it does not verify, will silently accept a proof anchored to the wrong shard). This is a concrete corrupted RPC result: the `outcome_root_proof` returned by the public JSON-RPC endpoint is cryptographically invalid for the claimed outcome.

### Likelihood Explanation
NEAR mainnet has already undergone resharding (4 → 5 shards). After any such event, every transaction executed in the epoch immediately before the split whose account maps to a child shard different from the parent (i.e., the right child) triggers this path. Any unprivileged user querying `tx`, `EXPERIMENTAL_tx_status`, or `GetExecutionOutcome` for such a transaction receives the wrong proof. No special privileges are required; the trigger is a standard public RPC call.

### Recommendation
After `get_next_block_hash_with_new_chunk` returns `(h, target_shard_id)`, recompute `target_shard_index` using the shard layout of block `h`:

```rust
if let Some((h, target_shard_id)) = res {
    outcome_proof.block_hash = h;
    let h_epoch_id = *self.chain.get_block_header(&h)?.epoch_id();
    let h_shard_layout = self.epoch_manager.get_shard_layout(&h_epoch_id)?;
    let target_shard_index = h_shard_layout.get_shard_index(target_shard_id)?;
    let outcome_roots = ...;
    ...
    outcome_root_proof: merklize(&outcome_roots).1[target_shard_index].clone(),
}
```

### Proof of Concept
1. Run a network with epoch length = 10 and a resharding scheduled at epoch 2 (shard 0 → shards 0 and 1, boundary account `"m"`).
2. In epoch 1, send a transaction from account `"z.near"` (maps to shard 0 in old layout, maps to shard 1 in new layout after split).
3. Wait for the transaction to be included and for resharding to activate in epoch 2.
4. Call `GetExecutionOutcome` for the transaction hash.
5. Observe that `outcome_root_proof` is a Merkle path for shard 0's `prev_outcome_root` in the first new-chunk block of epoch 2, not shard 1's. Attempting to verify the proof against shard 1's outcome root fails. [3](#0-2) [4](#0-3)

### Citations

**File:** chain/client/src/view_client_actor.rs (L1148-1183)
```rust
                let epoch_id =
                    *self.chain.get_block(&outcome_proof.block_hash)?.header().epoch_id();
                let shard_layout =
                    self.epoch_manager.get_shard_layout(&epoch_id).into_chain_error()?;
                let target_shard_id =
                    account_id_to_shard_id(self.epoch_manager.as_ref(), &account_id, &epoch_id)
                        .into_chain_error()?;
                let target_shard_index = shard_layout
                    .get_shard_index(target_shard_id)
                    .map_err(Into::into)
                    .into_chain_error()?;
                let res = self.chain.get_next_block_hash_with_new_chunk(
                    &outcome_proof.block_hash,
                    target_shard_id,
                )?;
                if let Some((h, target_shard_id)) = res {
                    outcome_proof.block_hash = h;
                    // Here we assume the number of shards is small so this reconstruction
                    // should be fast
                    let outcome_roots = self
                        .chain
                        .get_block(&h)?
                        .chunks()
                        .iter()
                        .map(|header| *header.prev_outcome_root())
                        .collect::<Vec<_>>();
                    if target_shard_index >= outcome_roots.len() {
                        return Err(GetExecutionOutcomeError::InconsistentState {
                            number_or_shards: outcome_roots.len(),
                            execution_outcome_shard_id: target_shard_id,
                        });
                    }
                    Ok(GetExecutionOutcomeResponse {
                        outcome_proof: outcome_proof.into(),
                        outcome_root_proof: merklize(&outcome_roots).1[target_shard_index].clone(),
                    })
```

**File:** chain/chain/src/chain.rs (L3916-3944)
```rust
            let next_epoch_id = *self.get_block_header(&next_block_hash)?.epoch_id();
            if next_epoch_id != epoch_id {
                let next_shard_layout = self.epoch_manager.get_shard_layout(&next_epoch_id)?;
                if next_shard_layout != shard_layout {
                    shard_ids = shard_ids
                        .into_iter()
                        .flat_map(|id| {
                            next_shard_layout.get_children_shards_ids(id).unwrap_or_else(|| {
                                panic!("invalid shard layout {:?} because it does not contain children shards for parent shard {}", next_shard_layout, id)
                            })
                        })
                        .collect();

                    shard_layout = next_shard_layout;
                }
                epoch_id = next_epoch_id;
            }
            block_hash = next_block_hash;

            let block = self.get_block(&block_hash)?;
            let chunks = block.chunks();
            for &shard_id in &shard_ids {
                let shard_index = shard_layout.get_shard_index(shard_id)?;
                let chunk_header =
                    &chunks.get(shard_index).ok_or(Error::InvalidShardId(shard_id))?;
                if chunk_header.height_included() == block.header().height() {
                    return Ok(Some((block_hash, shard_id)));
                }
            }
```
