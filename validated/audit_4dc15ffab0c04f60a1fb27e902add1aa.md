### Title
Stale Shard Index Used to Select `outcome_root_proof` After Resharding — (`File: chain/client/src/view_client_actor.rs`)

### Summary

`GetExecutionOutcome` computes `target_shard_index` from the shard layout of the **original execution block**, then uses that index to select the merkle path from `outcome_roots` collected from a **later block that may belong to a different, post-resharding epoch**. After a resharding event the child shard occupies a different position in the new layout, so the wrong merkle path is returned as `outcome_root_proof`, making every light-client proof for outcomes executed before the reshard permanently unverifiable.

### Finding Description

In `chain/client/src/view_client_actor.rs`, the `GetExecutionOutcome` handler computes `target_shard_index` once, from the epoch of the block that originally recorded the outcome:

```rust
let epoch_id = *self.chain.get_block(&outcome_proof.block_hash)?.header().epoch_id();
let shard_layout = self.epoch_manager.get_shard_layout(&epoch_id)...;
let target_shard_id =
    account_id_to_shard_id(self.epoch_manager.as_ref(), &account_id, &epoch_id)...;
let target_shard_index = shard_layout.get_shard_index(target_shard_id)...;
``` [1](#0-0) 

It then calls `get_next_block_hash_with_new_chunk`, which explicitly handles resharding by walking forward until it finds a block in the **new epoch** that contains a new chunk for one of the child shards, and returns the new child `shard_id`:

```rust
let res = self.chain.get_next_block_hash_with_new_chunk(
    &outcome_proof.block_hash,
    target_shard_id,
)?;
if let Some((h, target_shard_id)) = res {   // target_shard_id is now a child shard
``` [2](#0-1) 

The `outcome_roots` vector is then built from block `h` (the new-epoch block), whose chunks are ordered by the **new** shard layout:

```rust
let outcome_roots = self.chain.get_block(&h)?.chunks().iter()
    .map(|header| *header.prev_outcome_root())
    .collect::<Vec<_>>();
...
outcome_root_proof: merklize(&outcome_roots).1[target_shard_index].clone(),
``` [3](#0-2) 

`target_shard_index` is never recomputed for the new epoch. After resharding, the child shard occupies a different position in the new layout's chunk ordering, so `target_shard_index` (from the old layout) selects the wrong element of `outcome_roots`, producing an incorrect `outcome_root_proof`.

`get_next_block_hash_with_new_chunk` explicitly handles the resharding case by mapping parent shards to child shards: [4](#0-3) 

### Impact Explanation

The corrupted value is `outcome_root_proof` — the `MerklePath` field of `GetExecutionOutcomeResponse` that is returned directly by the `EXPERIMENTAL_light_client_proof` JSON-RPC endpoint. A light client or bridge contract verifies:

```
block_outcome_root = compute_root(sha256(borsh(shard_outcome_root)), outcome_root_proof)
```

With the wrong merkle path, this reconstruction yields a hash that does not match `block_header_lite.inner_lite.outcome_root`, so every proof for a pre-reshard outcome fails verification. Bridges relying on NEAR light-client proofs cannot confirm that legitimate transactions were executed, permanently blocking cross-chain withdrawals or message relays for those outcomes.

### Likelihood Explanation

Resharding is a planned, recurring protocol event on NEAR mainnet. Any transaction or receipt executed in the epoch immediately before a resharding boundary is affected. The trigger is a normal public RPC call (`EXPERIMENTAL_light_client_proof`) made by any user after resharding completes — no special privileges are required.

### Recommendation

After `get_next_block_hash_with_new_chunk` returns `(h, new_target_shard_id)`, recompute `target_shard_index` using the shard layout of block `h`:

```rust
if let Some((h, target_shard_id)) = res {
    outcome_proof.block_hash = h;
    let new_epoch_id = *self.chain.get_block(&h)?.header().epoch_id();
    let new_shard_layout = self.epoch_manager.get_shard_layout(&new_epoch_id)...;
    let target_shard_index = new_shard_layout.get_shard_index(target_shard_id)...;
    ...
}
```

### Proof of Concept

1. Deploy a two-shard network scheduled to reshard (shard 1 splits into shards 2 and 3).
2. Submit a transaction from an account in shard 1 at height `H` (pre-reshard epoch).
3. Wait for resharding to complete; the account now belongs to shard 2 (new index 1) or shard 3 (new index 2) in the new layout.
4. Call `EXPERIMENTAL_light_client_proof` for the transaction hash.
5. The handler finds the next block with a new chunk for the child shard (e.g., shard 3, new index 2), but uses `target_shard_index = 1` (old index of shard 1) to index into `outcome_roots`.
6. The returned `outcome_root_proof` is the merkle path for shard 2's outcome root, not shard 3's.
7. Verification: `compute_root(sha256(borsh(shard3_outcome_root)), wrong_proof) ≠ block_outcome_root` — proof fails.

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

**File:** chain/client/src/view_client_actor.rs (L1159-1164)
```rust
                let res = self.chain.get_next_block_hash_with_new_chunk(
                    &outcome_proof.block_hash,
                    target_shard_id,
                )?;
                if let Some((h, target_shard_id)) = res {
                    outcome_proof.block_hash = h;
```

**File:** chain/client/src/view_client_actor.rs (L1167-1183)
```rust
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

**File:** chain/chain/src/chain.rs (L3904-3944)
```rust
    pub fn get_next_block_hash_with_new_chunk(
        &self,
        block_hash: &CryptoHash,
        shard_id: ShardId,
    ) -> Result<Option<(CryptoHash, ShardId)>, Error> {
        let mut block_hash = *block_hash;
        let mut epoch_id = *self.get_block_header(&block_hash)?.epoch_id();
        let mut shard_layout = self.epoch_manager.get_shard_layout(&epoch_id)?;
        // this corrects all the shard where the original shard will split to if sharding changes
        let mut shard_ids = vec![shard_id];

        while let Ok(next_block_hash) = self.chain_store.get_next_block_hash(&block_hash) {
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
