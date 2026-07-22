### Title
Missing State Diff Hash Verification Against `state_diff_commitment` in P2P Sync — (`crates/apollo_p2p_sync/src/client/state_diff.rs`)

### Summary

`StateDiffStreamBuilder::parse_data_for_block` validates only the *length* of peer-supplied state diff chunks against the stored `state_diff_length`, but never computes `calculate_state_diff_hash` on the assembled diff and compares it to the stored `state_diff_commitment`. Any unauthenticated p2p peer can therefore feed a length-correct but content-wrong state diff that is accepted and written to storage, leaving the node with a `ThinStateDiff` whose Poseidon hash does not match the `state_diff_commitment` recorded in the block header.

---

### Finding Description

**Header reception path** (`header.rs`):

`HeaderStreamBuilder::parse_data_for_block` accepts a `SignedBlockHeader` from a peer and stores it after checking only two things: that the block number is sequential, and that exactly one signature is present. [1](#0-0) 

There is no verification that:
- the block hash is correctly derived from the header fields,
- the signature is cryptographically valid against a known sequencer key, or
- `state_diff_commitment` and `state_diff_length` are mutually consistent.

**State diff reception path** (`state_diff.rs`):

`StateDiffStreamBuilder::parse_data_for_block` reads `target_state_diff_len` from the already-stored header and collects chunks until the accumulated length equals that target. [2](#0-1) 

After the loop, the only post-collection check is `validate_deprecated_declared_classes_non_conflicting`. There is **no call to `calculate_state_diff_hash`** and no comparison against the stored `state_diff_commitment`. [3](#0-2) 

The assembled diff is then written directly to storage: [4](#0-3) 

**The committer does have a hash check**, but it is (a) in a separate component not invoked by the p2p sync client, and (b) gated behind a `verify_state_diff_hash` config flag: [5](#0-4) 

---

### Impact Explanation

A syncing node that stores a corrupted `ThinStateDiff` will serve wrong answers for every state query that touches that block: `starknet_getStorageAt`, `starknet_getNonce`, `starknet_getClassHashAt`, fee estimation, simulation, and tracing. The stored `state_diff_commitment` in the header will not match the actual stored diff, so the node's view of the chain state silently diverges from the canonical chain. This maps to **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value**, and potentially **Critical — wrong storage value / class hash** for any downstream execution that reads from the corrupted state.

---

### Likelihood Explanation

The attack requires only that the adversary be a reachable p2p peer of the target node. No authentication, no validator key, no privileged position is needed. The node actively queries peers for state diff data; any peer that responds first with a length-matching but content-wrong diff wins. The `state_diff_length` field is a small integer that is trivially matched by crafting chunks whose `len()` sum equals the target.

---

### Recommendation

After the collection loop in `StateDiffStreamBuilder::parse_data_for_block`, compute the Poseidon hash of the assembled diff and compare it to the `state_diff_commitment` stored in the header:

```rust
let stored_commitment = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    .expect("header must exist")
    .state_diff_commitment
    .ok_or(P2pSyncClientError::OldHeaderInStorage { ... })?;

let computed = calculate_state_diff_hash(&result);
if computed != stored_commitment {
    return Err(ParseDataError::BadPeer(BadPeerError::StateDiffHashMismatch { ... }));
}
```

This mirrors the existing check in `apollo_committer` [6](#0-5) 
and should be unconditional (not config-gated) in the p2p sync path.

---

### Proof of Concept

1. Node starts syncing from block 0. It queries a peer for headers.
2. Attacker (peer) sends a valid-looking `SignedBlockHeader` for block 0 with:
   - `state_diff_commitment = H(diff_A)` (Poseidon hash of some legitimate diff)
   - `state_diff_length = len(diff_B)` where `len(diff_B) == len(diff_A)` but `diff_B ≠ diff_A`
   - Signature count = 1 (passes the only signature check at line 115 of `header.rs`) [7](#0-6) 
3. Header is stored. Node now queries for state diff for block 0.
4. Attacker sends `diff_B` chunks whose total `len()` equals `target_state_diff_len`.
5. The loop at line 72 of `state_diff.rs` exits when `current_state_diff_len == target_state_diff_len`. [8](#0-7) 
6. `diff_B` passes `validate_deprecated_declared_classes_non_conflicting` and is written to storage.
7. **Assert**: `calculate_state_diff_hash(stored_diff_B) ≠ stored state_diff_commitment` — the node's storage is now inconsistent.

Even without controlling the header (i.e., with a legitimately stored header carrying the real `state_diff_commitment = H(diff_A)` and `state_diff_length = len(diff_A)`), step 4 onward still succeeds as long as the attacker can supply a `diff_B` with `len(diff_B) == len(diff_A)` and `diff_B ≠ diff_A`, because the hash check is entirely absent from the state diff sync path.

### Citations

**File:** crates/apollo_p2p_sync/src/client/header.rs (L104-120)
```rust
            if block_number
                != signed_block_header.block_header.block_header_without_hash.block_number
            {
                return Err(ParseDataError::BadPeer(BadPeerError::HeadersUnordered {
                    expected_block_number: block_number,
                    actual_block_number: signed_block_header
                        .block_header
                        .block_header_without_hash
                        .block_number,
                }));
            }
            if signed_block_header.signatures.len() != ALLOWED_SIGNATURES_LENGTH {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongSignaturesLength {
                    signatures: signed_block_header.signatures,
                }));
            }
            Ok(Some(signed_block_header))
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L33-35)
```rust
        async move {
            storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
            STATE_SYNC_STATE_MARKER.set_lossy(self.1.unchecked_next().0);
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-104)
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

            while current_state_diff_len < target_state_diff_len {
                let maybe_state_diff_chunk = state_diff_chunks_response_manager
                    .next()
                    .await
                    .ok_or(ParseDataError::BadPeer(BadPeerError::SessionEndedWithoutFin {
                        type_description: Self::TYPE_DESCRIPTION,
                    }))?;
                let Some(state_diff_chunk) = maybe_state_diff_chunk?.0 else {
                    if current_state_diff_len == 0 {
                        return Ok(None);
                    } else {
                        return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength {
                            expected_length: target_state_diff_len,
                            possible_lengths: vec![current_state_diff_len],
                        }));
                    }
                };
                prev_result_len = current_state_diff_len;
                if state_diff_chunk.is_empty() {
                    return Err(ParseDataError::BadPeer(BadPeerError::EmptyStateDiffPart));
                }
                // It's cheaper to calculate the length of `state_diff_part` than the length of
                // `result`.
                current_state_diff_len += state_diff_chunk.len();
                unite_state_diffs(&mut result, state_diff_chunk)?;
            }

            if current_state_diff_len != target_state_diff_len {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength {
                    expected_length: target_state_diff_len,
                    possible_lengths: vec![prev_result_len, current_state_diff_len],
                }));
            }
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L106-108)
```rust
            validate_deprecated_declared_classes_non_conflicting(&result)?;
            Ok(Some((result, block_number)))
        }
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
