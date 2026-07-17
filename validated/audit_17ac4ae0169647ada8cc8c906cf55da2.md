Now I have all the code I need. Let me trace the exact bug path.

### Title
Stale `target_shard_index` from old epoch layout used to index `outcome_roots` from new epoch block after resharding — (`chain/client/src/view_client_actor.rs`)

### Summary

The `GetExecutionOutcome` handler computes `target_shard_index` once from the **old epoch's** shard layout, then calls `get_next_block_hash_with_new_chunk`, which — at a resharding boundary — returns a block in the **new epoch** and a **child shard ID**. The handler then indexes into `outcome_roots` collected from that new-epoch block using the stale old-epoch index, producing a Merkle proof for the wrong shard's outcome root. Any unprivileged RPC client whose receipt crosses the resharding boundary will receive a structurally invalid light-client proof.

---

### Finding Description

**Step 1 — `target_shard_index` is fixed from the old epoch layout.** [1](#0-0) 

`epoch_id` is the epoch of the block where the outcome was recorded (old epoch). `shard_layout` is that epoch's layout. `target_shard_index` is the position of the parent shard (e.g. `S0 → index 0`) in the old layout. This value is **never updated** for the rest of the function.

**Step 2 — `get_next_block_hash_with_new_chunk` expands to child shards and returns a new-epoch block.** [2](#0-1) 

When the next block crosses an epoch boundary with a different shard layout, `shard_ids` is replaced with the child shards of the parent (e.g. `[S0]` → `[S2, S3]`) and `shard_layout` is updated to the new layout. The function then returns `(block_hash_in_new_epoch, child_shard_id)`. [3](#0-2) 

**Step 3 — The caller uses the stale old-epoch index against new-epoch `outcome_roots`.** [4](#0-3) 

`target_shard_id` is shadowed with the child shard ID returned by `get_next_block_hash_with_new_chunk`, but `target_shard_index` is **not recomputed**. `outcome_roots` is built from block `h` (in the new epoch), which has a different number of chunks and a different shard ordering. The proof is then built using the old index.

---

### Impact Explanation

Concrete layout mismatch:

| Layout | Shards | Indices |
|--------|--------|---------|
| Old (epoch N) | `[S0, S1]` | S0→0, S1→1 |
| New (epoch N+1, S0 split) | `[S1, S2, S3]` | S1→0, S2→1, S3→2 |

For a receipt executed in S0 (epoch N):
- `target_shard_index = 0` (from old layout, for S0)
- `get_next_block_hash_with_new_chunk` returns `(h, S2)` (first child with a new chunk)
- `outcome_roots` from block `h` = `[S1_root, S2_root, S3_root]`
- `outcome_roots[0]` = `S1_root` — **wrong shard**

The returned `outcome_root_proof` is a Merkle proof that `S1_root` is in the block's outcome root. But `outcome_proof` proves the execution outcome is in `S2_root`. Light-client verification fails because the two proofs reference different shards' roots. Every `EXPERIMENTAL_light_client_proof` call for a receipt whose shard was split returns a cryptographically invalid proof.

---

### Likelihood Explanation

- Reachable by any unprivileged RPC caller: submit a receipt with `receiver_id` in a shard that undergoes a split, wait for the resharding epoch boundary, then call `EXPERIMENTAL_light_client_proof` with the receipt ID.
- No validator or admin privileges required.
- Triggered deterministically at every resharding boundary for any receipt in the split shard.

---

### Recommendation

After `get_next_block_hash_with_new_chunk` returns `(h, target_shard_id)`, recompute `target_shard_index` using the epoch layout of block `h`:

```rust
if let Some((h, target_shard_id)) = res {
    outcome_proof.block_hash = h;
    // Recompute index using the layout of the block that contains the new chunk,
    // which may be a different epoch (and different shard layout) than the outcome block.
    let h_epoch_id = *self.chain.get_block(&h)?.header().epoch_id();
    let h_shard_layout = self.epoch_manager.get_shard_layout(&h_epoch_id).into_chain_error()?;
    let target_shard_index = h_shard_layout
        .get_shard_index(target_shard_id)
        .map_err(Into::into)
        .into_chain_error()?;
    // ... rest unchanged
```

---

### Proof of Concept

Construct a two-epoch test scenario:

1. Old epoch: 2 shards `[S0, S1]`. Submit a receipt with `receiver_id` in S0. Record `receipt_id` and `outcome_proof.block_hash` (in old epoch).
2. Advance past the resharding boundary. New epoch: 3 shards `[S1, S2, S3]` (S0 split).
3. Call `GetExecutionOutcome { id: Receipt { receipt_id, receiver_id } }`.
4. Assert that `get_next_block_hash_with_new_chunk` returns a block in the new epoch with `child_shard_id ∈ {S2, S3}`.
5. Assert that `target_shard_index` (0, from old layout) ≠ `new_layout.get_shard_index(child_shard_id)` (1 or 2).
6. Verify the returned `outcome_root_proof` against the block's actual outcome root — it will fail because it proves the wrong shard's root.

### Citations

**File:** chain/client/src/view_client_actor.rs (L1148-1158)
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
```

**File:** chain/client/src/view_client_actor.rs (L1163-1183)
```rust
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

**File:** chain/chain/src/chain.rs (L3917-3932)
```rust
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
```

**File:** chain/chain/src/chain.rs (L3941-3943)
```rust
                if chunk_header.height_included() == block.header().height() {
                    return Ok(Some((block_hash, shard_id)));
                }
```
