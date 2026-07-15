### Title
Missing Bounds Check on `sub_epoch_n` Index into `summaries` in Weight Proof Segment Validation — (File: `chia/full_node/weight_proof.py`)

---

### Summary

In `_validate_sub_epoch_segments`, the `sub_epoch_n` value sourced from attacker-controlled `WeightProof.sub_epoch_segments` is used as a direct index into the `summaries` list (derived from `WeightProof.sub_epochs`) without any bounds check. A malicious peer can craft a `WeightProof` where `sub_epoch_n` in a segment exceeds `len(summaries) - 1`, causing an unhandled `IndexError` that crashes weight proof validation on any syncing full node or wallet node.

---

### Finding Description

`_validate_sub_epoch_segments` receives two independent collections from the same `WeightProof` object sent by a peer:

- `summaries`: built from `weight_proof.sub_epochs` — length N
- `segments_by_sub_epoch`: built from `weight_proof.sub_epoch_segments` — keys are `segment.sub_epoch_n` values, which are fully attacker-controlled

The function iterates over `segments_by_sub_epoch.items()` and at each iteration uses `sub_epoch_n` as a direct index into `summaries`:

```python
for sub_epoch_n, segments in segments_by_sub_epoch.items():
    ...
    if sub_epoch_n > 0:
        ...
        prev_ses = summaries[sub_epoch_n - 1]   # line 983
        ...
    if not summaries[sub_epoch_n].reward_chain_hash == rc_sub_slot_hash:  # line 985
```

There is no check that `sub_epoch_n < len(summaries)` before either access. An attacker who sends a `WeightProof` with:
- `sub_epochs` = [1 entry] → `summaries` has length 1
- `sub_epoch_segments` = [one segment with `sub_epoch_n = 5`]

will cause `summaries[5]` at line 985 to raise `IndexError: list index out of range`.

The `map_segments_by_sub_epoch` helper at line 1679 simply reads `segment.sub_epoch_n` directly from the deserialized peer message with no validation:

```python
def map_segments_by_sub_epoch(sub_epoch_segments):
    for idx, segment in enumerate(sub_epoch_segments):
        if curr_sub_epoch_n < segment.sub_epoch_n:
            curr_sub_epoch_n = segment.sub_epoch_n
            segments[curr_sub_epoch_n] = []
        segments[curr_sub_epoch_n].append(segment)
    return segments
```

The `IndexError` is unhandled. Both callers treat the return value as `None`-or-list, not as a potential exception:

- `validate_weight_proof_single_proc` (line 596): `if _validate_sub_epoch_segments(...) is None:`
- `validate_weight_proof_inner` (line 1749): `vdfs_to_validate = _validate_sub_epoch_segments(...)`

An exception propagates through both paths, crashing the validation task.

---

### Impact Explanation

**High — Permanent or long-lived inability for honest nodes and wallets to process sync updates.**

Weight proof validation is the mechanism by which light clients (wallets) and newly-syncing full nodes establish trust in the chain tip. A malicious peer that serves a crafted `WeightProof` causes an unhandled `IndexError` in the validation coroutine. If the node's sync logic does not catch this exception at a higher level and blacklist the peer, the node may repeatedly fail to sync. A network-level attacker controlling multiple peers can sustain this denial of sync against a target node.

---

### Likelihood Explanation

Any peer on the Chia network can serve a `WeightProof` during the sync handshake. The `WeightProof` is a streamable type received over the wire; `sub_epoch_n` inside `SubEpochChallengeSegment` is a `uint32` field with no protocol-level constraint tying it to the length of `sub_epochs`. Crafting the malicious proof requires no keys, no privileged access, and no cryptographic capability — only the ability to connect to a target node and respond to a weight proof request.

---

### Recommendation

Add an explicit bounds check before indexing `summaries` with `sub_epoch_n` inside `_validate_sub_epoch_segments`:

```python
for sub_epoch_n, segments in segments_by_sub_epoch.items():
    if sub_epoch_n >= len(summaries):
        log.error(
            f"sub_epoch_n {sub_epoch_n} out of range for summaries of length {len(summaries)}"
        )
        return None
    ...
```

This mirrors the fix recommended in the external report: add an explicit equality/bounds check before using one collection's index to access another.

---

### Proof of Concept

1. Attacker connects to a syncing full node or wallet node.
2. Attacker responds to a weight proof request with a crafted `WeightProof`:
   - `sub_epochs`: a single valid-looking `SubEpochData` entry (so `summaries` has length 1 after `_validate_sub_epoch_summaries`)
   - `sub_epoch_segments`: a single `SubEpochChallengeSegment` with `sub_epoch_n = 10`
3. The victim node calls `validate_weight_proof` → `validate_weight_proof_inner` → `_validate_sub_epoch_segments`.
4. `map_segments_by_sub_epoch` produces `{10: [segment]}`.
5. The loop reaches `summaries[10]` at line 985 with `len(summaries) == 1`.
6. Python raises `IndexError: list index out of range`, propagating up through the validation stack. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** chia/full_node/weight_proof.py (L593-597)
```python
            log.error("failed weight proof sub epoch sample validation")
            return False, uint32(0)

        if _validate_sub_epoch_segments(self.constants, rng, wp_segment_bytes, summary_bytes, peak_height) is None:
            return False, uint32(0)
```

**File:** chia/full_node/weight_proof.py (L969-987)
```python
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
```

**File:** chia/full_node/weight_proof.py (L1679-1689)
```python
def map_segments_by_sub_epoch(
    sub_epoch_segments: list[SubEpochChallengeSegment],
) -> dict[int, list[SubEpochChallengeSegment]]:
    segments: dict[int, list[SubEpochChallengeSegment]] = {}
    curr_sub_epoch_n = -1
    for idx, segment in enumerate(sub_epoch_segments):
        if curr_sub_epoch_n < segment.sub_epoch_n:
            curr_sub_epoch_n = segment.sub_epoch_n
            segments[curr_sub_epoch_n] = []
        segments[curr_sub_epoch_n].append(segment)
    return segments
```

**File:** chia/full_node/weight_proof.py (L1748-1755)
```python
    if not skip_segment_validation:
        vdfs_to_validate = _validate_sub_epoch_segments(
            constants, rng, wp_segment_bytes, summary_bytes, peak_height, validate_from
        )
        await asyncio.sleep(0)  # break up otherwise multi-second sync code

        if vdfs_to_validate is None:
            return False, []
```

**File:** chia/wallet/wallet_weight_proof_handler.py (L45-67)
```python
    async def validate_weight_proof(
        self, weight_proof: WeightProof, skip_segment_validation: bool = False, old_proof: WeightProof | None = None
    ) -> list[BlockRecord]:
        start_time = time.time()
        summaries, sub_epoch_weight_list = _validate_sub_epoch_summaries(self._constants, weight_proof)
        await asyncio.sleep(0)  # break up otherwise multi-second sync code
        if summaries is None or sub_epoch_weight_list is None:
            raise ValueError("weight proof failed sub epoch data validation")
        validate_from = get_fork_ses_idx(old_proof, weight_proof)
        valid, block_records = await validate_weight_proof_inner(
            self._constants,
            self._executor,
            self._executor_shutdown_tempfile.name,
            self._num_processes,
            weight_proof,
            summaries,
            sub_epoch_weight_list,
            skip_segment_validation,
            validate_from,
        )
        if not valid:
            raise ValueError("weight proof validation failed")
        log.info(f"It took {time.time() - start_time} time to validate the weight proof {weight_proof.get_hash()}")
```
