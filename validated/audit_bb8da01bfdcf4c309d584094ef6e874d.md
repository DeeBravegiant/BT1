### Title
Malicious Weight Proof with Challenge Block at Sub-Slot Index 0 Causes `AssertionError` Crash in `__get_cc_sub_slot` — (`File: chia/full_node/weight_proof.py`)

### Summary

`__get_cc_sub_slot` in `chia/full_node/weight_proof.py` contains an unguarded `assert sub_slot is not None` that fires when the function is called with `idx=0`. A malicious peer can craft a `WeightProof` containing a segment (in any sub-epoch `> 0`) whose challenge block sits at `sub_slots[0]`, triggering an `AssertionError` that crashes weight-proof validation and prevents the victim node from syncing.

### Finding Description

`__get_cc_sub_slot` iterates `reversed(range(idx))` to find the preceding slot-end entry:

```python
# chia/full_node/weight_proof.py  lines 1532-1540
def __get_cc_sub_slot(sub_slots, idx, ses):
    sub_slot: SubSlotData | None = None
    for i in reversed(range(idx)):          # empty when idx == 0
        sub_slot = sub_slots[i]
        if sub_slot.cc_slot_end_info is not None:
            break

    assert sub_slot is not None             # AssertionError when idx == 0
    assert sub_slot.cc_slot_end_info is not None
```

When `idx == 0`, `reversed(range(0))` produces an empty iterator, `sub_slot` stays `None`, and the bare `assert` raises `AssertionError`.

The caller, `__validate_pospace`, guards this only for the genesis sub-epoch:

```python
# lines 1401-1404
if first_in_sub_epoch and segment.sub_epoch_n == 0 and idx == 0:
    cc_sub_slot_hash = constants.GENESIS_CHALLENGE
else:
    cc_sub_slot_hash = __get_cc_sub_slot(segment.sub_slots, idx, ses).get_hash()
```

For any segment with `sub_epoch_n > 0`, there is no guard when `idx == 0`. [1](#0-0) 

The sibling function `__get_rc_sub_slot` had the identical class of bug (documented as SEC-614) and was fixed to return `None` gracefully when `idx < 0`. [2](#0-1)  That fix only covers `__get_rc_sub_slot`; `__get_cc_sub_slot` retains the crashing assert. [3](#0-2) 

The existing test explicitly documents the pre-fix / post-fix boundary for `__get_rc_sub_slot` but does not cover the `__get_cc_sub_slot` path: [4](#0-3) 

### Impact Explanation

`validate_weight_proof` is called during long-range sync. An `AssertionError` propagates out of `_validate_sub_epoch_segments` → `validate_weight_proof_inner` → `validate_weight_proof` and is caught in `request_validate_wp`:

```python
# chia/full_node/full_node.py  lines 1183-1190
try:
    validated, fork_point, summaries = await self.weight_proof_handler.validate_weight_proof(response.wp)
except Exception as e:
    await weight_proof_peer.close(CONSENSUS_ERROR_BAN_SECONDS)
    raise ValueError(f"Weight proof validation threw an error {e}")
``` [5](#0-4) 

The victim node bans the peer and aborts the sync attempt. An attacker controlling multiple peers (e.g., via a Sybil attack or by operating eclipse nodes) can repeatedly deliver crafted proofs, preventing the victim from ever completing a weight-proof-based sync. This is a **High** impact: long-lived inability for honest nodes to sync under normal network assumptions.

### Likelihood Explanation

- Any unprivileged peer that participates in the Chia p2p network can respond to `RequestProofOfWeight` with an arbitrary `WeightProof`.
- The attacker only needs to craft a segment in `sub_epoch_n > 0` where `sub_slots[0].cc_slot_end is None` (challenge block at position 0) and ensure that segment is the sampled one. Because `sampled_seg_index = rng.choice(range(len(segments)))` is deterministic given the proof's sub-epoch summaries, the attacker can compute which index will be sampled and place the malicious segment there. [6](#0-5) 
- `__get_rc_sub_slot` is called only on `segments[0]`; placing the malicious segment at a non-zero index bypasses that guard entirely. [7](#0-6) 

### Recommendation

Replace the bare asserts in `__get_cc_sub_slot` with explicit `None` checks that return `None` (or raise a typed exception), mirroring the fix already applied to `__get_rc_sub_slot`:

```python
def __get_cc_sub_slot(sub_slots, idx, ses):
    sub_slot: SubSlotData | None = None
    for i in reversed(range(idx)):
        sub_slot = sub_slots[i]
        if sub_slot.cc_slot_end_info is not None:
            break

    if sub_slot is None or sub_slot.cc_slot_end_info is None:
        log.error("malformed segment: no slot-end entry before challenge block (cc)")
        return None   # caller must handle None
    ...
```

Callers of `__get_cc_sub_slot` (currently `__validate_pospace`) must be updated to propagate `None` upward rather than calling `.get_hash()` on a potentially `None` return value.

### Proof of Concept

1. Obtain a valid `WeightProof` from an honest peer.
2. Find any segment with `sub_epoch_n > 1`.
3. Compute `sampled_seg_index` for that sub-epoch using the deterministic RNG seeded from `summaries[-2].get_hash()`.
4. Replace `segments[sampled_seg_index].sub_slots` with a list whose first entry has `cc_slot_end = None` (i.e., a challenge-block entry at index 0).
5. Send the modified proof to a syncing victim node via `RespondProofOfWeight`.
6. `_validate_segment` reaches `idx=0` for the sampled segment, calls `__validate_pospace(..., idx=0, ...)`, which calls `__get_cc_sub_slot(sub_slots, 0, ses)`, triggering `AssertionError: assert sub_slot is not None` at line 1539.
7. The victim node catches the exception, bans the peer, and fails to complete sync. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** chia/full_node/weight_proof.py (L977-984)
```python
        sampled_seg_index = rng.choice(range(len(segments)))
        if sub_epoch_n > 0:
            rc_sub_slot = __get_rc_sub_slot(constants, segments[0], summaries, curr_ssi)
            if rc_sub_slot is None:
                log.error(f"failed to reconstruct rc sub slot for sub_epoch {sub_epoch_n}")
                return None
            prev_ses = summaries[sub_epoch_n - 1]
            rc_sub_slot_hash = rc_sub_slot.get_hash()
```

**File:** chia/full_node/weight_proof.py (L1017-1051)
```python
def _validate_segment(
    constants: ConsensusConstants,
    segment: SubEpochChallengeSegment,
    curr_ssi: uint64,
    prev_ssi: uint64,
    curr_difficulty: uint64,
    ses: SubEpochSummary | None,
    first_segment_in_se: bool,
    sampled: bool,
    height: uint32,
) -> tuple[bool, int, int, int, list[tuple[VDFProof, ClassgroupElement, VDFInfo]]]:
    ip_iters, slot_iters, slots = 0, 0, 0
    after_challenge = False
    to_validate = []
    for idx, sub_slot_data in enumerate(segment.sub_slots):
        if sampled and sub_slot_data.is_challenge():
            after_challenge = True
            required_iters = __validate_pospace(
                constants, segment, idx, curr_difficulty, ses, first_segment_in_se, height
            )
            if required_iters is None:
                return False, uint64(0), uint64(0), uint64(0), []
            assert sub_slot_data.signage_point_index is not None
            ip_iters += calculate_ip_iters(constants, curr_ssi, sub_slot_data.signage_point_index, required_iters)
            vdf_list = _get_challenge_block_vdfs(constants, idx, segment.sub_slots, curr_ssi)
            to_validate.extend(vdf_list)
        elif sampled and after_challenge:
            validated, vdf_list = _validate_sub_slot_data(constants, idx, segment.sub_slots, curr_ssi)
            if not validated:
                log.error(f"failed to validate sub slot data {idx} vdfs")
                return False, uint64(0), uint64(0), uint64(0), []
            to_validate.extend(vdf_list)
        slot_iters += curr_ssi
        slots += uint64(1)
    return True, ip_iters, slot_iters, slots, to_validate
```

**File:** chia/full_node/weight_proof.py (L1392-1404)
```python
def __validate_pospace(
    constants: ConsensusConstants,
    segment: SubEpochChallengeSegment,
    idx: int,
    curr_diff: uint64,
    ses: SubEpochSummary | None,
    first_in_sub_epoch: bool,
    height: uint32,
) -> uint64 | None:
    if first_in_sub_epoch and segment.sub_epoch_n == 0 and idx == 0:
        cc_sub_slot_hash = constants.GENESIS_CHALLENGE
    else:
        cc_sub_slot_hash = __get_cc_sub_slot(segment.sub_slots, idx, ses).get_hash()
```

**File:** chia/full_node/weight_proof.py (L1491-1493)
```python
        if idx < 0:
            log.error("malformed segment: no slot-end entry before challenge block")
            return None
```

**File:** chia/full_node/weight_proof.py (L1532-1540)
```python
def __get_cc_sub_slot(sub_slots: list[SubSlotData], idx: int, ses: SubEpochSummary | None) -> ChallengeChainSubSlot:
    sub_slot: SubSlotData | None = None
    for i in reversed(range(idx)):
        sub_slot = sub_slots[i]
        if sub_slot.cc_slot_end_info is not None:
            break

    assert sub_slot is not None
    assert sub_slot.cc_slot_end_info is not None
```

**File:** chia/_tests/weight_proof/test_weight_proof.py (L668-715)
```python
        """SEC-614: validation must not crash when a segment's challenge block
        is the first sub-slot entry (first_idx == 0).

        In legitimately-constructed proofs, segment creation always places at
        least one slot-end entry before the challenge block (first_idx >= 1).
        A malicious peer could send a crafted proof where first_idx == 0; the
        old ``assert first_idx`` rejected this with an AssertionError instead
        of cleanly failing validation.
        """
        blocks = default_1000_blocks
        header_cache, height_to_hash, sub_blocks, summaries = await load_blocks_dont_validate(
            blocks, blockchain_constants
        )
        wpf = WeightProofHandler(
            blockchain_constants, BlockchainMock(sub_blocks, header_cache, height_to_hash, summaries)
        )
        wp = await wpf.get_proof_of_weight(blocks[-1].header_hash)
        assert wp is not None

        # Find the first segment of sub_epoch > 0 and strip the leading
        # slot-end entries so the challenge block lands at index 0.
        modified_segments = list(wp.sub_epoch_segments)
        target_found = False
        for i, seg in enumerate(modified_segments):
            if seg.sub_epoch_n > 0:
                challenge_idx = next(
                    (j for j, ssd in enumerate(seg.sub_slots) if ssd.cc_slot_end is None),
                    None,
                )
                if challenge_idx is not None and challenge_idx > 0:
                    new_sub_slots = list(seg.sub_slots[challenge_idx:])
                    assert new_sub_slots[0].cc_slot_end is None
                    modified_segments[i] = seg.replace(sub_slots=new_sub_slots)
                    target_found = True
                    break

        assert target_found, "No segment found with leading slot-end entries to strip"

        modified_wp = dataclasses.replace(wp, sub_epoch_segments=modified_segments)

        # Pre-fix: AssertionError in __get_rc_sub_slot crashes validation.
        # Post-fix: the malformed segment causes a hash mismatch, returning
        # (False, 0) cleanly.
        wpf_verify = WeightProofHandler(
            blockchain_constants, BlockchainMock(sub_blocks, header_cache, height_to_hash, {})
        )
        valid, _fork_point = wpf_verify.validate_weight_proof_single_proc(modified_wp)
        assert not valid
```

**File:** chia/full_node/full_node.py (L1183-1190)
```python
        try:
            validated, fork_point, summaries = await self.weight_proof_handler.validate_weight_proof(response.wp)
        except Exception as e:
            await weight_proof_peer.close(CONSENSUS_ERROR_BAN_SECONDS)
            raise ValueError(f"Weight proof validation threw an error {e}")
        if not validated:
            await weight_proof_peer.close(CONSENSUS_ERROR_BAN_SECONDS)
            raise ValueError("Weight proof validation failed")
```
