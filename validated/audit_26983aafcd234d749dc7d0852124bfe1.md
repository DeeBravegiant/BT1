### Title
Missing `state_diff_commitment` Validation in P2P State Diff Sync Allows Malicious Peer to Poison Node Storage — (`crates/apollo_p2p_sync/src/client/state_diff.rs`)

---

### Summary

`parse_data_for_block` in the P2P sync client validates only `state_diff_length` against the stored header value, but never reads or verifies `state_diff_commitment`. A malicious p2p peer can send a length-correct but content-forged state diff that is accepted and written directly to storage, causing the node to permanently serve wrong storage values via `starknet_getStorageAt`.

---

### Finding Description

`parse_data_for_block` reads `state_diff_length` from the stored header to gate how many chunks it accepts: [1](#0-0) 

It accumulates chunks until `current_state_diff_len == target_state_diff_len`, then calls `write_to_storage` with no further integrity check: [2](#0-1) 

`write_to_storage` calls `append_state_diff` directly — no Poseidon hash is computed, and `state_diff_commitment` from the header is never read: [3](#0-2) 

The header sync path compounds this: `parse_data_for_block` for headers checks only block number ordering and signature vector length — it never calls `verify_block_signature` and never validates the block hash (which commits to `state_diff_commitment`): [4](#0-3) 

`calculate_state_diff_hash` and `verify_block_signature` are never called anywhere in the p2p sync client crate. The `verify_state_diff_hash` flag that exists in `apollo_committer` is entirely absent from this path: [5](#0-4) 

---

### Impact Explanation

A malicious p2p peer that responds to state diff queries can:

1. Send `N` `StateDiffChunk::ContractDiff` messages whose combined `len()` equals `target_state_diff_len`, but whose `storage_diffs` contain attacker-chosen `(StorageKey, Felt)` pairs.
2. The node accepts and stores the forged diff because the only gate is the length counter.
3. Every subsequent `starknet_getStorageAt` call for the affected contract/key returns the forged value as authoritative state.

This matches: **High — RPC execution returns an authoritative-looking wrong value**, and potentially **Critical — wrong storage value stored and used by execution logic**.

---

### Likelihood Explanation

Any unauthenticated p2p peer the victim node queries is a valid attacker. No special privilege is required. The node selects peers from the p2p network and sends them `StateDiffQuery` messages; the peer's response is the sole source of truth for the state diff content. There is no fallback verification against L1 data or the stored `state_diff_commitment` at any point in this pipeline.

---

### Recommendation

After assembling the full `ThinStateDiff` (after the `while` loop), compute its Poseidon hash and compare it against `state_diff_commitment` read from the stored header, mirroring the pattern already used in `apollo_committer`:

```rust
// After the while loop in parse_data_for_block:
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
    return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffCommitment { ... }));
}
```

Additionally, the header sync should verify the block signature (using `verify_block_signature`) so that the `state_diff_commitment` stored in the header is itself authenticated.

---

### Proof of Concept

1. Store a header for block 0 with `state_diff_length = 1` and a known `state_diff_commitment` (Poseidon hash of a canonical diff containing `(contract_A, key_K) -> value_canonical`).
2. As the p2p state diff peer, send one `StateDiffChunk::ContractDiff` for `contract_A` with `storage_diffs = {key_K: value_forged}` (length = 1, matching `target_state_diff_len`).
3. Assert `parse_data_for_block` returns `Ok(Some(...))` — it does, because only the length is checked.
4. Assert `get_storage_at(contract_A, key_K)` returns `value_forged` — it does, because the forged diff was written to storage without commitment verification. [6](#0-5)

### Citations

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L28-39)
```rust
    fn write_to_storage<'a>(
        self: Box<Self>,
        storage_writer: &'a mut StorageWriter,
        _class_manager_client: &'a mut SharedClassManagerClient,
    ) -> BoxFuture<'a, Result<(), P2pSyncClientError>> {
        async move {
            storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
            STATE_SYNC_STATE_MARKER.set_lossy(self.1.unchecked_next().0);
            Ok(())
        }
        .boxed()
    }
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L51-110)
```rust
    fn parse_data_for_block<'a>(
        state_diff_chunks_response_manager: &'a mut ClientResponsesManager<
            DataOrFin<StateDiffChunk>,
        >,
        block_number: BlockNumber,
        storage_reader: &'a StorageReader,
    ) -> BoxFuture<'a, Result<Option<Self::Output>, ParseDataError>> {
        async move {
            let mut result = ThinStateDiff::default();
            let mut prev_result_len = 0;
            let mut current_state_diff_len = 0;
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

            validate_deprecated_declared_classes_non_conflicting(&result)?;
            Ok(Some((result, block_number)))
        }
        .boxed()
    }
```

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
