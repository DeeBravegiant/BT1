The code is clear. Let me confirm there is no hash verification anywhere in the pipeline before writing.

The two grep searches are definitive: `state_diff_commitment` is never referenced in `state_diff.rs`, and `calculate_state_diff_hash` is never called anywhere in the entire `apollo_p2p_sync` crate. The missing verification is confirmed.

---

### Title
Missing Poseidon Hash Verification of Peer-Supplied State Diff Allows Storage Corruption — (`crates/apollo_p2p_sync/src/client/state_diff.rs`)

### Summary
`parse_data_for_block` validates only that the cumulative `StateDiffChunk::len()` equals `header.state_diff_length`, but never computes `calculate_state_diff_hash` on the assembled `ThinStateDiff` and never compares it against `header.state_diff_commitment`. A single malicious or compromised peer can therefore cause a syncing node to persist a `ThinStateDiff` whose Poseidon commitment diverges from the canonical `state_diff_commitment` stored in the block header.

### Finding Description

`parse_data_for_block` reads exactly one field from the stored header:

```rust
// state_diff.rs line 62-70
let target_state_diff_len = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    .expect("...")
    .state_diff_length          // ← only the length is read
    .ok_or(...)?;
``` [1](#0-0) 

The loop accumulates chunks until `current_state_diff_len == target_state_diff_len` and then returns `Ok(Some((result, block_number)))` with no further integrity check:

```rust
// state_diff.rs line 106-107
validate_deprecated_declared_classes_non_conflicting(&result)?;
Ok(Some((result, block_number)))   // ← no hash check before returning
``` [2](#0-1) 

`write_to_storage` then unconditionally persists whatever `ThinStateDiff` was assembled:

```rust
// state_diff.rs line 34
storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
``` [3](#0-2) 

`calculate_state_diff_hash` is never called anywhere in the `apollo_p2p_sync` crate. The `state_diff_commitment` field of the `BlockHeader` — which holds the canonical Poseidon commitment — is never read during state diff sync. The verification that does exist in `apollo_committer` (`verify_state_diff_hash` flag in `commit_or_load`) is entirely separate and is not invoked on the P2P sync path. [4](#0-3) 

### Impact Explanation

A node syncing via P2P will store a `ThinStateDiff` that is internally consistent (no duplicate keys, correct entry count) but cryptographically wrong relative to the block header's `state_diff_commitment`. Downstream consequences:

1. **Wrong storage values served by RPC** — any `starknet_getStorageAt` / `starknet_getStateUpdate` call for the affected block returns attacker-chosen values.
2. **Wrong global state root** — the Patricia trie is updated from the corrupted diff, producing a `global_root` that does not match the canonical chain, breaking proof verification and any downstream commitment check.
3. **Persistent, silent corruption** — because the stored header's `state_diff_commitment` is never re-checked against the stored diff after the fact, the corruption is not self-healing.

### Likelihood Explanation

Any peer the victim node connects to can mount this attack. P2P peers are unauthenticated and low-trust by design. The attacker only needs to:
- Serve a valid header (or let the victim sync it from an honest peer first),
- Then respond to the state diff query with chunks whose `len()` values sum correctly but whose key/value content is substituted.

No validator, operator, or privileged role is required.

### Recommendation

After the accumulation loop completes and before returning `Ok(Some(...))`, compute and verify the Poseidon commitment:

```rust
use starknet_api::block_hash::state_diff_hash::calculate_state_diff_hash;

let expected_commitment = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    .expect("...")
    .state_diff_commitment
    .ok_or(P2pSyncClientError::OldHeaderInStorage {
        block_number,
        missing_field: "state_diff_commitment",
    })?;

let actual_commitment = calculate_state_diff_hash(&result);
if actual_commitment != expected_commitment {
    return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffHash {
        expected: expected_commitment,
        actual: actual_commitment,
    }));
}
```

This mirrors the pattern already used in `apollo_committer::commit_or_load`. [5](#0-4) 

### Proof of Concept

1. Write a block header with a known `state_diff_commitment = calculate_state_diff_hash(&canonical_diff)` and `state_diff_length = canonical_diff.len()`.
2. Construct substitute chunks: same total `len()`, but replace one `StorageKey`/value pair with attacker-chosen values.
3. Feed the substitute chunks through `parse_data_for_block` — the length check passes, `validate_deprecated_declared_classes_non_conflicting` passes.
4. Call `write_to_storage`.
5. Assert `calculate_state_diff_hash(stored_diff) != header.state_diff_commitment` — the assertion holds, confirming the corrupted diff was persisted.

### Citations

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L34-34)
```rust
            storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-70)
```rust
            let target_state_diff_len = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_number)?
                .expect("A header with number lower than the header marker is missing")
                .state_diff_length
                .ok_or(P2pSyncClientError::OldHeaderInStorage {
                    block_number,
                    missing_field: "state_diff_length",
                })?;
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L106-107)
```rust
            validate_deprecated_declared_classes_non_conflicting(&result)?;
            Ok(Some((result, block_number)))
```

**File:** crates/apollo_committer/src/committer.rs (L265-280)
```rust
        let state_diff_commitment = match state_diff_commitment {
            Some(commitment) => {
                if self.config.verify_state_diff_hash {
                    let calculated_commitment = calculate_state_diff_hash(state_diff);
                    if commitment != calculated_commitment {
                        return Err(CommitterError::StateDiffHashMismatch {
                            provided_commitment: commitment,
                            calculated_commitment,
                            height,
                        });
                    }
                }
                commitment
            }
            None => calculate_state_diff_hash(state_diff),
        };
```
