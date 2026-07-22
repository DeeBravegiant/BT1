### Title
Missing State Diff Commitment Verification in P2P Sync Allows Malicious Peer to Corrupt Node Storage — (`crates/apollo_p2p_sync/src/client/state_diff.rs`)

---

### Summary

`StateDiffStreamBuilder::parse_data_for_block` assembles a `ThinStateDiff` from peer-supplied chunks and writes it directly to storage without ever verifying that `calculate_state_diff_hash(&result)` equals the `state_diff_commitment` already stored in the block header. Combined with the equally unvalidated header ingestion path in `HeaderStreamBuilder::parse_data_for_block`, a single malicious sync peer can inject a completely fabricated state diff for any block, permanently corrupting `get_storage_at` / `get_nonce` / `get_class_hash_at` RPC results for that block.

---

### Finding Description

**Phase 1 — Header injection (no cryptographic guard).**

`HeaderStreamBuilder::parse_data_for_block` accepts a `SignedBlockHeader` from the network and stores it after only two checks:

- The embedded `block_number` matches the expected sequence number.
- `signatures.len() == ALLOWED_SIGNATURES_LENGTH` (length only; the signature bytes are never verified against the block hash or any sequencer public key). [1](#0-0) 

There is no verification of `block_hash`, no parent-hash chain check (acknowledged as a TODO), and no check that `state_diff_commitment` or `state_diff_length` are consistent with any trusted anchor (L1, consensus, or a quorum of peers). The header — including its attacker-chosen `state_diff_length = L` and `state_diff_commitment` — is written verbatim to storage. [2](#0-1) 

**Phase 2 — State diff injection (no commitment check).**

`StateDiffStreamBuilder::parse_data_for_block` reads `target_state_diff_len` directly from the previously stored (attacker-controlled) header: [3](#0-2) 

It then loops, consuming exactly `target_state_diff_len` units of peer-supplied `StateDiffChunk` data: [4](#0-3) 

After the loop, the only post-assembly check is `validate_deprecated_declared_classes_non_conflicting` — a structural deduplication guard, not a cryptographic one. There is **no call to `calculate_state_diff_hash`** and no comparison against `block_header.state_diff_commitment`. The assembled `ThinStateDiff` is then committed unconditionally: [5](#0-4) 

The missing guard — present in the committer for its own flow but absent here — would be:

```rust
// This check does NOT exist in parse_data_for_block:
let calculated = calculate_state_diff_hash(&result);
if calculated != stored_header.state_diff_commitment.unwrap() {
    return Err(ParseDataError::BadPeer(BadPeerError::StateDiffCommitmentMismatch { ... }));
}
```

The committer does implement this check behind a `verify_state_diff_hash` config flag, but that path is never reached for P2P-synced state diffs: [6](#0-5) 

---

### Impact Explanation

After the two-phase attack, the node's storage contains a `ThinStateDiff` for block N whose content is entirely attacker-controlled. Every subsequent RPC call that reads from that block's state — `get_storage_at`, `get_nonce`, `get_class_hash_at` — returns the injected value. Because the header's `state_diff_commitment` field is also attacker-controlled (no signature verification), the stored commitment does not contradict the stored diff; the node has no internal signal that anything is wrong. The corruption is permanent until the node re-syncs from a trusted source.

---

### Likelihood Explanation

The attacker needs only to be the node's sync peer for the target block range — achievable via eclipse attack, Sybil peering, or by being the first peer the node connects to on a fresh sync. No validator key, sequencer key, or operator privilege is required. The two-phase sequence (header then state diff) is the normal protocol flow; the attacker simply substitutes fabricated data.

---

### Recommendation

In `StateDiffStreamBuilder::parse_data_for_block`, after the assembly loop and before returning `Ok(Some(...))`, add:

```rust
let stored_commitment = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    .expect("header must exist")
    .state_diff_commitment
    .ok_or(P2pSyncClientError::OldHeaderInStorage {
        block_number,
        missing_field: "state_diff_commitment",
    })?;

let calculated_commitment = calculate_state_diff_hash(&result);
if calculated_commitment != stored_commitment {
    return Err(ParseDataError::BadPeer(BadPeerError::StateDiffCommitmentMismatch {
        expected: stored_commitment,
        actual: calculated_commitment,
        block_number: block_number.0,
    }));
}
```

Additionally, `HeaderStreamBuilder::parse_data_for_block` should verify the block signature cryptographically (not just its length) against a known sequencer public key, and verify `block_hash` against an L1-anchored or consensus-anchored source.

---

### Proof of Concept

```
Phase 1 — inject header:
  peer sends SignedBlockHeader {
      block_header: BlockHeader {
          block_number: N,
          state_diff_length: Some(5),          // inflated / attacker-chosen
          state_diff_commitment: Some(FAKE),   // arbitrary; never verified
          block_hash: FAKE_HASH,               // never verified
          ...
      },
      signatures: vec![BlockSignature::default()],  // length==1 passes the only check
  }
  → node stores header with state_diff_length=5, state_diff_commitment=FAKE

Phase 2 — inject state diff:
  peer sends 5 StateDiffChunk::ContractDiff chunks, each with:
      contract_address = TARGET_CONTRACT,
      storage_diffs = { TARGET_KEY => INJECTED_VALUE }
  → parse_data_for_block counts 5 units, passes all structural checks,
    calls append_state_diff(N, fabricated_thin_state_diff)

Assertion:
  storage_reader.begin_ro_txn()
      .get_storage_at(TARGET_CONTRACT, TARGET_KEY, N)
      == INJECTED_VALUE   // ✓ corruption confirmed
```

### Citations

**File:** crates/apollo_p2p_sync/src/client/header.rs (L34-50)
```rust
            storage_writer
                .begin_rw_txn()?
                .append_header(
                    self.block_header.block_header_without_hash.block_number,
                    &self.block_header,
                )?
                .append_block_signature(
                    self.block_header.block_header_without_hash.block_number,
                    self
                    .signatures
                    // In the future we will support multiple signatures.
                    .first()
                    // The verification that the size of the vector is 1 is done in the data
                    // verification.
                    .expect("Vec::first should return a value on a vector of size 1"),
                )?
                .commit()?;
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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L33-35)
```rust
        async move {
            storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
            STATE_SYNC_STATE_MARKER.set_lossy(self.1.unchecked_next().0);
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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L72-107)
```rust
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
