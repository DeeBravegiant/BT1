After thorough investigation of the Chia codebase, I found a genuine analog to the Optimism H-01 "wrong context passed to validator" bug class.

---

### Title
Weight Proof Segment PoSpace Validated Against Peak Height Instead of Actual Block Height — (`File: chia/full_node/weight_proof.py`)

### Summary

In `__validate_pospace`, the `height` argument used to compute the plot-filter prefix bits is `peak_height` (the tip of the weight proof's recent chain), not the actual height of the block inside the sampled sub-epoch segment. Because the plot filter becomes progressively looser as height increases (fewer prefix bits required), old blocks deep in the chain are validated against a weaker filter than the one that was in force when those blocks were supposedly farmed. A malicious peer can craft a weight proof whose sampled segment contains a proof-of-space that passes the current (looser) filter but would have been rejected by the stricter historical filter, causing a syncing node to accept a forged weight proof.

### Finding Description

The call chain is:

1. `validate_weight_proof_single_proc` / `validate_weight_proof_inner` computes `peak_height` as the height of the last block in `recent_chain_data` and passes it to `_validate_sub_epoch_segments`. [1](#0-0) 

2. `_validate_sub_epoch_segments` forwards that same `height` (still `peak_height`) unchanged to every call of `_validate_segment`, regardless of which sub-epoch the segment belongs to. [2](#0-1) 

3. `_validate_segment` forwards it to `__validate_pospace`. [3](#0-2) 

4. `__validate_pospace` calls `validate_pospace_and_get_required_iters` with `height_agnostic=True` (to skip v1 phase-out / v2 activation checks) but still passes `height` (= `peak_height`) as the block height, which is then forwarded to `verify_and_get_quality_string`. [4](#0-3) 

5. Inside `verify_and_get_quality_string`, `height` is used to compute `prefix_bits` for the plot filter — the only remaining height-sensitive check. [5](#0-4) 

6. `calculate_prefix_bits` reduces the number of required leading zero bits as height crosses `HARD_FORK_HEIGHT`, `PLOT_FILTER_128_HEIGHT`, `PLOT_FILTER_64_HEIGHT`, and `PLOT_FILTER_32_HEIGHT`. [6](#0-5) 

A segment from sub-epoch 0 (height ≈ 0) is therefore validated with the plot filter of the chain tip (e.g., 5 prefix bits at `PLOT_FILTER_32_HEIGHT`) instead of the 9 prefix bits that were in force at genesis. This is a 16× relaxation of the filter, giving an attacker a 16× larger pool of plots from which to find a proof-of-space that satisfies the challenge hash.

### Impact Explanation

The weight proof is the mechanism by which a syncing node establishes trust in a chain without downloading every block. If a malicious peer can supply a weight proof whose sampled segment contains a proof-of-space that passes the current (looser) filter but would have failed the historical filter, the syncing node accepts the proof and may sync to a fake chain. This maps directly to "Forged weight proof trust" in the allowed Critical impact scope.

### Likelihood Explanation

The attacker must also supply valid VDF proofs for the sampled segment, which is computationally expensive. However, the plot-filter bypass materially reduces the difficulty of finding a usable proof-of-space for a crafted challenge, lowering the bar for a well-resourced attacker. The entry path is fully unprivileged: any peer can send a `WeightProof` message to a syncing node.

### Recommendation

Pass the actual block height of the sampled challenge block — derivable from `segment.sub_epoch_n * constants.SUB_EPOCH_BLOCKS` — to `__validate_pospace` instead of `peak_height`. Alternatively, extend `height_agnostic=True` to also skip the plot-filter check and instead validate the filter separately using the reconstructed block height.

### Proof of Concept

```
peak_height = 20_000_000   # past PLOT_FILTER_32_HEIGHT → prefix_bits = 5
segment.sub_epoch_n = 0    # actual block height ≈ 0 → correct prefix_bits = 9

# Attacker finds a plot that passes passes_plot_filter(5, ...) but NOT passes_plot_filter(9, ...)
# __validate_pospace uses peak_height=20_000_000, so prefix_bits=5 → filter passes
# The proof-of-space would have been rejected at the real block height (prefix_bits=9)
# validate_weight_proof_single_proc returns (True, fork_point) → syncing node trusts the fake chain
``` [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** chia/full_node/weight_proof.py (L585-596)
```python
        peak_height = weight_proof.recent_chain_data[-1].reward_chain_block.height
        log.info(f"validate weight proof peak height {peak_height}")
        summary_bytes, wp_segment_bytes, wp_recent_chain_bytes = vars_to_bytes(summaries, weight_proof)
        log.info("validate sub epoch challenge segments")
        seed = summaries[-2].get_hash()
        rng = random.Random(seed)
        assert sub_epoch_weight_list is not None
        if not validate_sub_epoch_sampling(rng, sub_epoch_weight_list, weight_proof):
            log.error("failed weight proof sub epoch sample validation")
            return False, uint32(0)

        if _validate_sub_epoch_segments(self.constants, rng, wp_segment_bytes, summary_bytes, peak_height) is None:
```

**File:** chia/full_node/weight_proof.py (L993-1004)
```python
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
```

**File:** chia/full_node/weight_proof.py (L1017-1035)
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
```

**File:** chia/full_node/weight_proof.py (L1392-1443)
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

    sub_slot_data: SubSlotData = segment.sub_slots[idx]

    if sub_slot_data.signage_point_index and is_overflow_block(constants, sub_slot_data.signage_point_index):
        if idx < 1:
            log.error("overflow block at index 0 has no previous sub slot")
            return None
        curr_slot = segment.sub_slots[idx - 1]
        assert curr_slot.cc_slot_end_info
        challenge = curr_slot.cc_slot_end_info.challenge
    else:
        challenge = cc_sub_slot_hash

    if sub_slot_data.cc_sp_vdf_info is None:
        cc_sp_hash = cc_sub_slot_hash
    else:
        cc_sp_hash = sub_slot_data.cc_sp_vdf_info.output.get_hash()

    # validate proof of space
    assert sub_slot_data.proof_of_space is not None

    # when sampling blocks as part of weight proof validation, the previous
    # transaction height is a conservative estimate, since we don't have direct
    # access to it.
    required_iters = validate_pospace_and_get_required_iters(
        constants,
        sub_slot_data.proof_of_space,
        challenge,
        cc_sp_hash,
        height,
        curr_diff,
        uint32(0),  # not used, since height_agnostic=True
        height_agnostic=True,
    )
    if required_iters is None:
        log.error("could not verify proof of space")
        return None

    return required_iters
```

**File:** chia/types/blockchain_format/proof_of_space.py (L122-165)
```python
def verify_and_get_quality_string(
    pos: ProofOfSpace,
    constants: ConsensusConstants,
    original_challenge_hash: bytes32,
    signage_point: bytes32,
    *,
    height: uint32,
    prev_transaction_block_height: uint32,  # this is the height of the last tx block before the current block SP
    height_agnostic: bool = False,
) -> bytes32 | None:
    plot_param = pos.param()

    if not height_agnostic:
        if plot_param.size_v1 is not None and is_v1_phased_out(pos.proof, prev_transaction_block_height, constants):
            log.info("v1 proof has been phased-out and is no longer valid")
            return None

        if plot_param.strength_v2 is not None and prev_transaction_block_height < constants.HARD_FORK2_HEIGHT:
            log.info("v2 proof support has not yet activated")
            return None

    # Exactly one of (pool_public_key, pool_contract_puzzle_hash) must not be None
    if (pos.pool_public_key is None) and (pos.pool_contract_puzzle_hash is None):
        log.error("Expected pool public key or pool contract puzzle hash but got neither")
        return None
    if (pos.pool_public_key is not None) and (pos.pool_contract_puzzle_hash is not None):
        log.error("Expected pool public key or pool contract puzzle hash but got both")
        return None

    if not check_plot_param(constants, plot_param):
        return None

    plot_id: bytes32 = pos.compute_plot_id()
    new_challenge: bytes32 = calculate_pos_challenge(plot_id, original_challenge_hash, signage_point)

    if new_challenge != pos.challenge:
        log.error(f"Calculated pos challenge doesn't match the provided one {new_challenge}")
        return None

    # we use different plot filter prefix sizes depending on v1 or v2 plots
    prefix_bits = calculate_prefix_bits(constants, height, plot_param)
    if not passes_plot_filter(prefix_bits, plot_id, original_challenge_hash, signage_point):
        log.error(f"Did not pass the plot filter. prefix bits: {prefix_bits} {'V1' if plot_param.size_v1 else 'V2'}")
        return None
```

**File:** chia/types/blockchain_format/proof_of_space.py (L211-232)
```python
def calculate_prefix_bits(constants: ConsensusConstants, height: uint32, plot_param: PlotParam) -> int:
    if plot_param.strength_v2 is not None:
        prefix_bits = int(constants.NUMBER_ZERO_BITS_PLOT_FILTER_V2)
        if height >= constants.PLOT_FILTER_V2_THIRD_ADJUSTMENT_HEIGHT:
            prefix_bits -= 3
        elif height >= constants.PLOT_FILTER_V2_SECOND_ADJUSTMENT_HEIGHT:
            prefix_bits -= 2
        elif height >= constants.PLOT_FILTER_V2_FIRST_ADJUSTMENT_HEIGHT:
            prefix_bits -= 1
        return max(0, prefix_bits)

    prefix_bits = int(constants.NUMBER_ZERO_BITS_PLOT_FILTER_V1)
    if height >= constants.PLOT_FILTER_32_HEIGHT:
        prefix_bits -= 4
    elif height >= constants.PLOT_FILTER_64_HEIGHT:
        prefix_bits -= 3
    elif height >= constants.PLOT_FILTER_128_HEIGHT:
        prefix_bits -= 2
    elif height >= constants.HARD_FORK_HEIGHT:
        prefix_bits -= 1

    return max(0, prefix_bits)
```
