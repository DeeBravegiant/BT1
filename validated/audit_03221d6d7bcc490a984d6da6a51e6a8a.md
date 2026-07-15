### Title
Predictable Sampling Seed Enables Selective VDF Forgery in Weight Proof — (File: `chia/full_node/weight_proof.py`)

### Summary
The weight proof sampling RNG is seeded exclusively from `summaries[-2].get_hash()`, a value the prover knows before constructing the proof. Because `sub_epoch_segments` are not committed to in the seed, the prover can pre-compute which segment index within each sub-epoch will be selected for VDF validation, then craft a weight proof that only includes valid VDF proofs at those sampled positions while placing fabricated VDF proofs at all other positions. Non-sampled segments receive zero VDF validation.

### Finding Description

In `validate_weight_proof_single_proc` and `validate_weight_proof_inner`, the RNG seed is derived solely from the second-to-last sub-epoch summary hash: [1](#0-0) [2](#0-1) 

This same `rng` object is then passed to `_validate_sub_epoch_segments`, which uses it to pick which segment within each sub-epoch has its VDFs validated: [3](#0-2) 

Inside `_validate_segment`, when `sampled=False`, the loop body performs **no VDF validation whatsoever** — it only accumulates `slot_iters` and `slots` counters: [4](#0-3) 

The prover-supplied `sub_epoch_segments` list is never included in the seed computation. Because the seed is fully determined before the prover assembles the segment list, the prover can:

1. Compute `seed = summaries[-2].get_hash()` (known from the chain).
2. Simulate the RNG through `_get_weights_for_sampling` (whose call count depends on `recent_chain_data`, also known to the prover) to determine the exact `sampled_seg_index` for every sub-epoch.
3. Construct the `sub_epoch_segments` list so that only the segment at `sampled_seg_index` carries valid VDF proofs; all other segments carry fabricated or zeroed VDF proofs.
4. Submit this weight proof to a syncing peer.

The only structural constraint on non-sampled segments is that `segments[0]` must reproduce the correct `rc_sub_slot_hash` matching the summary: [5](#0-4) 

Segments beyond index 0 that are not the sampled index face no VDF check at all.

### Impact Explanation

The weight proof is the sole mechanism by which a fast-syncing node establishes trust in a chain without replaying every block. If a prover can include fabricated VDF proofs in non-sampled segments, the syncing node accepts a weight proof attesting to VDF work that was never performed. This constitutes **forged weight proof trust**: the syncing node may follow a chain whose actual accumulated VDF work is far less than claimed, enabling a minority-hashrate attacker to present a fraudulent chain as the heaviest chain to nodes that rely on fast sync.

### Likelihood Explanation

The attack requires no special privileges. Any peer that can serve a weight proof to a syncing node can execute it. The seed is public and deterministic; computing the sampled indices requires only simulating Python's `random.Random` with the known seed, which is trivial. The attacker must possess a valid set of sub-epoch summaries (i.e., a real chain of some weight), but the fabricated non-sampled segments allow them to misrepresent the actual VDF work done in those sub-epochs.

### Recommendation

The seed must commit to all prover-supplied inputs that the verifier uses. Concretely, include a hash of the full `sub_epoch_segments` payload (and `recent_chain_data`) in the seed before initializing the RNG:

```python
# Instead of:
seed = summaries[-2].get_hash()

# Use something like:
seed = std_hash(
    summaries[-2].get_hash()
    + std_hash(bytes(weight_proof.sub_epoch_segments))
    + weight_proof.recent_chain_data[-1].header_hash
)
rng = random.Random(seed)
```

This ensures the prover cannot know the sampled indices before committing to the segment content, eliminating the ability to selectively place valid VDFs only at sampled positions.

### Proof of Concept

```python
# 1. Attacker obtains a valid WeightProof wp from the honest chain.
# 2. Compute the seed deterministically:
seed = summaries[-2].get_hash()          # summaries derived from wp.sub_epochs
rng = random.Random(seed)

# 3. Simulate _get_weights_for_sampling to exhaust the same RNG calls
#    the verifier will make, then for each sub-epoch compute:
for sub_epoch_n, segments in segments_by_sub_epoch.items():
    sampled_idx = rng.choice(range(len(segments)))
    # 4. Replace VDF proofs in all segments where idx != sampled_idx
    #    with zeroed/fabricated bytes. The verifier never checks them.
    for idx, seg in enumerate(segments):
        if idx != sampled_idx:
            segments[idx] = fabricate_segment_with_fake_vdfs(seg)

# 5. Submit the modified weight proof to a syncing node.
#    validate_weight_proof accepts it because only sampled segments
#    have their VDFs verified.
``` [6](#0-5)

### Citations

**File:** chia/full_node/weight_proof.py (L589-590)
```python
        seed = summaries[-2].get_hash()
        rng = random.Random(seed)
```

**File:** chia/full_node/weight_proof.py (L950-1013)
```python
def _validate_sub_epoch_segments(
    constants: ConsensusConstants,
    rng: random.Random,
    weight_proof_bytes: bytes,
    summaries_bytes: list[bytes],
    height: uint32,
    validate_from: int = 0,
) -> list[tuple[VDFProof, ClassgroupElement, VDFInfo]] | None:
    summaries = summaries_from_bytes(summaries_bytes)
    sub_epoch_segments: SubEpochSegments = SubEpochSegments.from_bytes(weight_proof_bytes)
    rc_sub_slot_hash = constants.GENESIS_CHALLENGE
    total_blocks, total_ip_iters = 0, 0
    total_slot_iters, total_slots = 0, 0
    total_ip_iters = 0
    prev_ses: SubEpochSummary | None = None
    segments_by_sub_epoch = map_segments_by_sub_epoch(sub_epoch_segments.challenge_segments)
    curr_ssi = constants.SUB_SLOT_ITERS_STARTING
    vdfs_to_validate = []
    max_segments = _max_sub_epoch_segments(constants)
    for sub_epoch_n, segments in segments_by_sub_epoch.items():
        if len(segments) > max_segments:
            log.error(f"sub_epoch {sub_epoch_n} has {len(segments)} segments, maximum allowed is {max_segments}")
            return None
        prev_ssi = curr_ssi
        curr_difficulty, curr_ssi = _get_curr_diff_ssi(constants, sub_epoch_n, summaries)
        log.debug(f"validate sub epoch {sub_epoch_n}")
        # recreate RewardChainSubSlot for next ses rc_hash
        sampled_seg_index = rng.choice(range(len(segments)))
        if sub_epoch_n > 0:
            rc_sub_slot = __get_rc_sub_slot(constants, segments[0], summaries, curr_ssi)
            if rc_sub_slot is None:
                log.error(f"failed to reconstruct rc sub slot for sub_epoch {sub_epoch_n}")
                return None
            prev_ses = summaries[sub_epoch_n - 1]
            rc_sub_slot_hash = rc_sub_slot.get_hash()
        if not summaries[sub_epoch_n].reward_chain_hash == rc_sub_slot_hash:
            log.error(f"failed reward_chain_hash validation sub_epoch {sub_epoch_n}")
            return None

        # skip validation up to fork height
        if sub_epoch_n < validate_from:
            continue

        for idx, segment in enumerate(segments):
            valid_segment, ip_iters, slot_iters, slots, vdf_list = _validate_segment(
                constants,
                segment,
                curr_ssi,
                prev_ssi,
                curr_difficulty,
                prev_ses,
                idx == 0,
                sampled_seg_index == idx,
                height,
            )
            vdfs_to_validate.extend(vdf_list)
            if not valid_segment:
                log.error(f"failed to validate sub_epoch {segment.sub_epoch_n} segment {idx} slots")
                return None
            prev_ses = None
            total_blocks += 1
            total_slot_iters += slot_iters
            total_slots += slots
            total_ip_iters += ip_iters
```

**File:** chia/full_node/weight_proof.py (L1031-1051)
```python
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

**File:** chia/full_node/weight_proof.py (L1731-1732)
```python
    seed = summaries[-2].get_hash()
    rng = random.Random(seed)
```
