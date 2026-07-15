The claim is concrete and traceable. Let me lay out the full analysis.

---

### Title
`future_sp_cache` Saturation via Unauthenticated Non-Normalized CC Proof Failures Drops Legitimate Signage Points — (`chia/full_node/full_node_store.py`)

### Summary

An unprivileged remote peer can send `RespondSignagePoint` messages whose `cc_proof.normalized_to_identity=False` and whose CC proof fails `validate_vdf`. The `new_signage_point` logic intentionally caches these as potentially-valid fork SPs via `add_to_future_sp`. Because the deduplication key is `(index, rc_vdf)` and the attacker can freely vary `rc_vdf.output`, 64 distinct garbage entries can be inserted under any chosen `rc_vdf.challenge`. Once the per-key limit is reached, `LRUKeyedListCache.append` silently returns `False`, and all subsequent legitimate SPs for that challenge are dropped. The peer is never banned because the result code is `NOT_ADDED`, not `INVALID_VDF`.

---

### Finding Description

**Step 1 — Entry point: `respond_signage_point`** [1](#0-0) 

The handler accepts any `RespondSignagePoint` from any peer. It checks `in_future_sp_cache` for deduplication, but that check uses `(index, rc_vdf)` equality: [2](#0-1) 

An attacker bypasses this by varying `rc_vdf.output` across messages while keeping `rc_vdf.challenge` fixed.

**Step 2 — Non-normalized CC proof failure path in `new_signage_point`**

When `cc_vdf.challenge` matches a known sub-slot and the structural check passes, but the CC proof is non-normalized and fails `validate_vdf`, the code explicitly caches the SP and returns `NOT_ADDED`: [3](#0-2) 

The comment at line 814–815 explains the design intent: a non-normalized proof failure is treated as ambiguous (the SP might be valid on a fork). This is the correct design for honest peers, but it creates an unauthenticated caching path for malicious ones.

Alternatively, if `cc_vdf.challenge` does not match any known sub-slot, the SP falls through unconditionally to: [4](#0-3) 

No validation at all is required in this path.

**Step 3 — `add_to_future_sp` inserts under `rc_vdf.challenge`** [5](#0-4) 

The cache key is `rc_vdf.challenge`. The attacker controls this field freely.

**Step 4 — Per-key limit silently drops legitimate entries** [6](#0-5) 

`FUTURE_SP_CACHE_MAX_ENTRIES_PER_KEY = 64`. After 64 entries for a given `rc_vdf.challenge`, all further `append` calls return `False` and the entry is silently discarded. [7](#0-6) 

**Step 5 — Peer is never banned** [8](#0-7) 

Banning only occurs for `INVALID_VDF`. `NOT_ADDED` (the result of the non-normalized CC proof failure path) causes the handler to return silently with no penalty.

**Step 6 — Legitimate SPs are lost at `new_peak` replay**

When the node finally receives the block, `new_peak` replays the future cache: [9](#0-8) 

If the cache for `peak.reward_infusion_new_challenge` was saturated with garbage, the legitimate SPs were never stored and are not replayed. Farmers never receive them.

**The existing test at line 1645–1647 explicitly confirms the caching behavior:** [10](#0-9) 

---

### Impact Explanation

The `future_sp_cache` is the mechanism by which signage points that arrive before their dependent block are preserved and forwarded to farmers after the block is received. Saturating the cache for a specific `rc_vdf.challenge` (which equals the current peak's `reward_infusion_new_challenge` — a publicly observable value) causes all legitimate SPs for the next block to be silently dropped. Farmers receive no signage points for that challenge and cannot produce proofs of space. The attacker can sustain the attack continuously (64 messages per challenge, refreshed before the 300-second TTL expires) with no ban risk. [11](#0-10) 

---

### Likelihood Explanation

- The target `rc_vdf.challenge` (`peak.reward_infusion_new_challenge`) is publicly visible on-chain.
- Only 64 unauthenticated messages are required per attack cycle.
- No cryptographic material, keys, or special privileges are needed.
- The attacker is never banned.
- The TTL is 300 seconds, so the attacker must resend every ~5 minutes, which is trivial.

---

### Recommendation

1. **Reject non-normalized CC proof failures as `INVALID_VDF`** rather than caching them, or at minimum require the RC VDF to be structurally consistent with the node's known chain state before caching.
2. **Rate-limit per-peer contributions to `future_sp_cache`**: track how many entries each peer has contributed and stop accepting from a peer that has filled a key.
3. **Validate `rc_vdf.challenge` against known chain state** before caching: only cache SPs whose `rc_vdf.challenge` is a plausible next-block reward challenge (e.g., matches `peak.reward_infusion_new_challenge` or a recently seen block's challenge).
4. **Ban peers** that repeatedly cause `NOT_ADDED` results with non-normalized CC proof failures, similar to how `INVALID_VDF` triggers a ban.

---

### Proof of Concept

```python
# Attacker fills future_sp_cache for target_challenge with 64 garbage entries.
# Each entry has a unique rc_vdf.output to bypass in_future_sp_cache dedup.

target_challenge = peak.reward_infusion_new_challenge  # publicly known

for i in range(FUTURE_SP_CACHE_MAX_ENTRIES_PER_KEY):  # 64 iterations
    garbage_output = ClassgroupElement(bytes([i % 256] * 100))
    rc_vdf = VDFInfo(target_challenge, uint64(1000), garbage_output)
    cc_vdf = VDFInfo(
        known_sub_slot_challenge,          # matches a finished sub-slot
        delta_iters,                       # correct for the chosen index
        ClassgroupElement.get_default_element(),
    )
    cc_proof = VDFProof(uint8(0), b"\xff" * 100, False)  # normalized_to_identity=False, invalid
    rc_proof = VDFProof(uint8(0), b"\x00" * 100, False)

    await peer.send_message(make_msg(
        ProtocolMessageTypes.respond_signage_point,
        RespondSignagePoint(uint8(1), cc_vdf, cc_proof, rc_vdf, rc_proof),
    ))
    # Result: NOT_ADDED, peer not banned, entry cached under target_challenge

# Now send a legitimate SP for target_challenge — it is silently dropped.
assert not store.in_future_sp_cache(legitimate_sp, uint8(1))
# future_sp_cache[target_challenge] is full; legitimate SP is lost.
```

### Citations

**File:** chia/full_node/full_node_api.py (L788-815)
```python
    @metadata.request(peer_required=True)
    async def respond_signage_point(
        self, request: full_node_protocol.RespondSignagePoint, peer: WSChiaConnection
    ) -> Message | None:
        if self.full_node.sync_store.get_sync_mode():
            return None
        async with self.full_node.timelord_lock:
            if self.full_node.full_node_store.have_newer_signage_point(
                request.challenge_chain_vdf.challenge,
                request.index_from_challenge,
                request.reward_chain_vdf.challenge,
            ):
                return None
            existing_sp = self.full_node.full_node_store.get_signage_point_by_index_and_cc_output(
                request.challenge_chain_vdf.output.get_hash(),
                request.challenge_chain_vdf.challenge,
                request.index_from_challenge,
            )
            if existing_sp is not None and existing_sp.rc_vdf == request.reward_chain_vdf:
                return None
            signage_point = SignagePoint(
                request.challenge_chain_vdf,
                request.challenge_chain_proof,
                request.reward_chain_vdf,
                request.reward_chain_proof,
            )
            if self.full_node.full_node_store.in_future_sp_cache(signage_point, request.index_from_challenge):
                return None
```

**File:** chia/full_node/full_node_api.py (L837-863)
```python
            if result == SignagePointAddResult.ADDED:
                await self.full_node.signage_point_post_processing(request, peer, ip_sub_slot)
                return None
            if result != SignagePointAddResult.INVALID_VDF:
                self.log.debug(
                    f"Signage point {request.index_from_challenge} not added, CC challenge: "
                    f"{request.challenge_chain_vdf.challenge.hex()}, "
                    f"RC challenge: {request.reward_chain_vdf.challenge.hex()}"
                )
                return None

        # INVALID_VDF: ban peer after releasing timelord_lock
        server = self.full_node.server
        peer_host = peer.peer_info.host
        if is_localhost(peer_host):
            self.log.debug(f"Not banning localhost peer for invalid signage point VDF proof: {peer_host}")
        elif server is not None and is_in_network(peer_host, server.exempt_peer_networks):
            self.log.debug(f"Not banning exempt network peer for invalid signage point VDF proof: {peer_host}")
        else:
            self.log.warning(
                f"Banning {peer.get_peer_logging()} for invalid signage point VDF proof. "
                f"SP index: {request.index_from_challenge}, "
                f"CC challenge: {request.challenge_chain_vdf.challenge.hex()}"
            )
            await peer.close(CONSENSUS_ERROR_BAN_SECONDS)

        return None
```

**File:** chia/full_node/full_node_store.py (L34-37)
```python
FUTURE_CACHE_ENTRY_TTL_SECONDS = 300

FUTURE_SP_CACHE_MAX_KEYS = 128
FUTURE_SP_CACHE_MAX_ENTRIES_PER_KEY = 64
```

**File:** chia/full_node/full_node_store.py (L381-390)
```python
    def in_future_sp_cache(self, signage_point: SignagePoint, index: uint8) -> bool:
        if signage_point.rc_vdf is None:
            return False

        if signage_point.rc_vdf.challenge not in self.future_sp_cache:
            return False
        for cache_index, cache_sp in self.future_sp_cache[signage_point.rc_vdf.challenge]:
            if cache_index == index and cache_sp.rc_vdf == signage_point.rc_vdf:
                return True
        return False
```

**File:** chia/full_node/full_node_store.py (L392-405)
```python
    def add_to_future_sp(self, signage_point: SignagePoint, index: uint8) -> None:
        if (
            signage_point.cc_vdf is None
            or signage_point.rc_vdf is None
            or signage_point.cc_proof is None
            or signage_point.rc_proof is None
        ):
            return None
        challenge = signage_point.rc_vdf.challenge
        if self.in_future_sp_cache(signage_point, index):
            return None
        if not self.future_sp_cache.append(challenge, (index, signage_point)):
            return None
        log.info(f"Don't have rc hash {challenge.hex()}. caching signage point {index}.")
```

**File:** chia/full_node/full_node_store.py (L813-823)
```python
                if not skip_vdf_validation:
                    # Non-normalized CC proofs are for a VDF segment. The SP's CC challenge/output can match while
                    # an honest peer proves from a different previous in-slot block than our current start element.
                    if not signage_point.cc_proof.normalized_to_identity and not validate_vdf(
                        signage_point.cc_proof,
                        self.constants,
                        start_ele,
                        cc_vdf_info_expected,
                    ):
                        self.add_to_future_sp(signage_point, index)
                        return SignagePointAddResult.NOT_ADDED
```

**File:** chia/full_node/full_node_store.py (L849-850)
```python
        self.add_to_future_sp(signage_point, index)
        return SignagePointAddResult.NOT_ADDED
```

**File:** chia/full_node/full_node_store.py (L1019-1032)
```python
        future_sps: list[tuple[uint8, SignagePoint]] = self.future_sp_cache.get(
            peak.reward_infusion_new_challenge, []
        ).copy()
        for index, sp in future_sps:
            assert sp.cc_vdf is not None
            if self.new_signage_point(index, blocks, peak, peak.sub_slot_iters, sp) == SignagePointAddResult.ADDED:
                new_sps.append((index, sp))

        for ip in self.future_ip_cache.get(peak.reward_infusion_new_challenge, []):
            new_ips.append(ip)

        self.future_eos_cache.pop(peak.reward_infusion_new_challenge)
        self.future_sp_cache.pop(peak.reward_infusion_new_challenge)
        self.future_ip_cache.pop(peak.reward_infusion_new_challenge)
```

**File:** chia/util/lru_cache.py (L131-135)
```python
        if len(entries) >= self._max_entries_per_key:
            return False, evicted_key
        entries.append(value)
        self._total_entries += 1
        return True, evicted_key
```

**File:** chia/_tests/core/full_node/stores/test_full_node_store.py (L1645-1647)
```python
    # Non-normalized CC proof failures are ambiguous when our peak is already inside the same sub-slot. The SP may
    # belong to a fork that does not include our current peak, so it should be cached rather than classified malicious.
    assert store.in_future_sp_cache(corrupted_sp2, uint8(2))
```
