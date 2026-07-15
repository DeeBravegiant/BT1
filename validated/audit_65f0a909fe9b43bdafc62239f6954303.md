Now I have all the information needed for a complete analysis. Let me trace the full attack path precisely.

**Key facts established:**

1. `peer_has_block` is called unconditionally at line 808 of `full_node.py` with the raw `request.header_hash` from a `NewPeak` message — before any block existence check.
2. `peak_to_peer` is an `OrderedDict` capped at 256 entries; each new unique hash evicts the oldest.
3. `get_peak_of_each_peer()` cross-checks `peer_to_peak` against `peak_to_peer` and silently drops any peer whose registered hash was evicted.
4. The `_sync()` wait loop polls `get_peak_of_each_peer()` every 0.1s for up to `max_sync_wait` (default 30s).
5. `new_peak` rate limit: **200 messages per 60 seconds per peer** (`RLSettings(True, 200, 512)`).

**Rate-limit arithmetic vs. attack requirement:**

With 2 sybil peers (200 msg/60s each = 400 combined), filling 256 slots takes ~38.4 seconds — slightly over the 30-second wait loop. However, the attacker can pre-flood *before* `_sync()` is triggered (honest peers connect → attacker starts flooding → `_sync()` fires later with honest hashes already evicted). With 3 sybil peers the eviction completes in ~25.6 seconds, well within the window. The rate limit does not prevent the attack; it only affects the minimum number of sybil connections needed.

---

### Title
Unauthenticated `NewPeak` Flood Evicts Honest Peers from `peak_to_peer`, Causing `_sync()` Wait Loop to Always Time Out — (`chia/full_node/sync_store.py`)

### Summary
A remote attacker controlling a small number of sybil peers can flood `SyncStore.peak_to_peer` with fabricated `header_hash` values via unauthenticated `NewPeak` protocol messages. Because `peak_to_peer` is a 256-entry `OrderedDict` that evicts the oldest entry on overflow, honest peers' hashes are silently dropped. `get_peak_of_each_peer()` then returns an empty result for honest peers, the `_sync()` wait loop never accumulates 3 peers at the same peak, and the node permanently fails to advance its chain tip as long as the flood is maintained.

### Finding Description

`peer_has_block` is called unconditionally with the raw, unvalidated `header_hash` from every incoming `NewPeak` message: [1](#0-0) 

Inside `peer_has_block`, each new unique hash creates a new `peak_to_peer` entry, and once the dict exceeds 256 entries the oldest is evicted: [2](#0-1) 

The only eviction guard is for `target_peak`, which is `None` during the wait loop (it is set only after the loop exits at line 1082): [3](#0-2) 

`get_peak_of_each_peer()` silently skips any peer whose registered hash is no longer in `peak_to_peer`: [4](#0-3) 

The `_sync()` wait loop uses this filtered result to decide whether to keep waiting: [5](#0-4) 

After the loop times out, `get_heaviest_peak()` applies the same `peak_to_peer` filter, so it either returns the sybil's fabricated peak (which fails weight-proof validation) or `None` (which raises `RuntimeError`), aborting sync either way: [6](#0-5) 

### Impact Explanation
As long as the attacker maintains sybil connections and keeps flooding, the victim node cannot complete a long sync. It loops: wait 30 s → abort → receive honest peak → trigger `_sync()` again → abort. The node is permanently stuck at its current chain tip, unable to process new valid blocks. This satisfies the High-impact criterion: "Permanent or long-lived inability for honest nodes to process valid blocks or sync updates under normal network assumptions."

### Likelihood Explanation
- Requires only standard peer connections (no special privileges).
- `NewPeak` messages are tiny (512-byte max) and rate-limited to 200/60s per peer — but 3 sybil peers suffice to evict 256 honest-peer hashes within the 30-second wait window (~25.6 s).
- Pre-flooding before `_sync()` fires makes even 2 sybil peers sufficient.
- No cryptographic material or chain knowledge is needed; `header_hash` is arbitrary `bytes32`.

### Recommendation
1. **Decouple `peak_to_peer` eviction from honest-peer hashes**: before evicting the oldest entry, check whether any peer in `peer_to_peak` still references that hash and skip eviction if so.
2. **Validate `header_hash` before storing**: at minimum, reject hashes whose claimed `weight`/`height` is implausible relative to the local chain tip before calling `peer_has_block`.
3. **Per-peer hash-change rate limiting in `SyncStore`**: track how many distinct hashes a single peer has contributed and cap it (e.g., 5–10 distinct peaks per peer per sync window).

### Proof of Concept

```python
# Pseudocode unit test (no network needed)
store = SyncStore()
honest_ids = [bytes32(i.to_bytes(32, 'big')) for i in range(3)]
honest_hash = bytes32(b'\xaa' * 32)

# Honest peers register their shared peak
for pid in honest_ids:
    store.peer_has_block(honest_hash, pid, uint128(1000), uint32(100), True)

assert len(store.get_peers_that_have_peak([honest_hash])) == 3  # passes

sybil_id = bytes32(b'\xbb' * 32)
# Flood with 257 unique fabricated hashes (new_peak=True each time)
for i in range(257):
    fake_hash = bytes32(i.to_bytes(32, 'big'))
    store.peer_has_block(fake_hash, sybil_id, uint128(9999), uint32(999), True)

# honest_hash is now evicted from peak_to_peer
peaks = [p.header_hash for p in store.get_peak_of_each_peer().values()]
assert honest_hash not in peaks                          # honest peers invisible
assert len(store.get_peers_that_have_peak(peaks)) < 3   # wait loop never exits
``` [7](#0-6) [8](#0-7)

### Citations

**File:** chia/full_node/full_node.py (L807-808)
```python
        # Store this peak/peer combination in case we want to sync to it, and to keep track of peers
        self.sync_store.peer_has_block(request.header_hash, peer.peer_node_id, request.weight, request.height, True)
```

**File:** chia/full_node/full_node.py (L1063-1071)
```python
            peaks = []
            for i in range(max_iterations):
                peaks = [peak.header_hash for peak in self.sync_store.get_peak_of_each_peer().values()]
                if len(self.sync_store.get_peers_that_have_peak(peaks)) < 3:
                    if self._shut_down:
                        return None
                    await asyncio.sleep(0.1)
                    continue
                break
```

**File:** chia/full_node/full_node.py (L1077-1080)
```python
            target_peak = self.sync_store.get_heaviest_peak()

            if target_peak is None:
                raise RuntimeError("Not performing sync, no peaks collected")
```

**File:** chia/full_node/sync_store.py (L59-79)
```python
    def peer_has_block(
        self, header_hash: bytes32, peer_id: bytes32, weight: uint128, height: uint32, new_peak: bool
    ) -> None:
        """
        Adds a record that a certain peer has a block.
        """

        if self.target_peak is not None and header_hash == self.target_peak.header_hash:
            self.peers_changed.set()
        if header_hash in self.peak_to_peer:
            self.peak_to_peer[header_hash].add(peer_id)
        else:
            self.peak_to_peer[header_hash] = {peer_id}
            if len(self.peak_to_peer) > 256:  # nice power of two
                item = self.peak_to_peer.popitem(last=False)  # Remove the oldest entry
                # sync target hash is used throughout the sync process and should not be deleted.
                if self.target_peak is not None and item[0] == self.target_peak.header_hash:
                    self.peak_to_peer[item[0]] = item[1]  # Put it back in if it was the sync target
                    self.peak_to_peer.popitem(last=False)  # Remove the oldest entry again
        if new_peak:
            self.peer_to_peak[peer_id] = Peak(header_hash, height, weight)
```

**File:** chia/full_node/sync_store.py (L98-103)
```python
        ret = {}
        for peer_id, peak in self.peer_to_peak.items():
            if peak.header_hash not in self.peak_to_peer:
                continue
            ret[peer_id] = peak
        return ret
```

**File:** chia/server/rate_limit_numbers.py (L94-94)
```python
        ProtocolMessageTypes.new_peak: RLSettings(True, 200, 512),
```
