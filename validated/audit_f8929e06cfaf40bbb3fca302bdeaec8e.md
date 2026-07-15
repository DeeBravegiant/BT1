### Title
Unguarded `assert` Statements in Weight Proof Segment Validation Reachable via Crafted Peer-Supplied `WeightProof` — (`File: chia/full_node/weight_proof.py`)

### Summary
Multiple bare `assert` statements inside weight proof segment validation functions (`__validate_pospace`, `_get_challenge_block_vdfs`, `__get_cc_sub_slot`) are reachable when a crafted `WeightProof` is supplied by a malicious peer. When triggered, they raise an unhandled `AssertionError` that propagates through `validate_weight_proof_inner`. The full node wraps the call in a `try/except Exception` in `request_validate_wp`, but the wallet's `WalletWeightProofHandler.validate_weight_proof` has no equivalent guard, leaving the wallet's sync path exposed to a crash from any peer it connects to.

### Finding Description

**Reachable assert 1 — `__validate_pospace`, line 1424:**
```python
assert sub_slot_data.proof_of_space is not None
```
Reached whenever `sampled and sub_slot_data.is_challenge()` is true in `_validate_segment`. A crafted segment whose challenge-block `SubSlotData` has `proof_of_space = None` triggers this unconditionally for the sampled segment. [1](#0-0) 

**Reachable assert 2 — `_get_challenge_block_vdfs`, lines 1073–1074:**
```python
assert sub_slot_data.cc_infusion_point
assert sub_slot_data.cc_ip_vdf_info
```
Called immediately after `__validate_pospace` returns for the sampled challenge block. A crafted challenge-block entry with `cc_infusion_point = None` or `cc_ip_vdf_info = None` fires these. [2](#0-1) 

**Reachable assert 3 — `__get_cc_sub_slot`, lines 1539–1540:**
```python
assert sub_slot is not None
assert sub_slot.cc_slot_end_info is not None
```
Called from `__validate_pospace` (line 1404) for any segment where the challenge block is not at `idx == 0` in sub-epoch 0. If no preceding `SubSlotData` in the segment has `cc_slot_end_info` set, the loop exits with `sub_slot` pointing to a slot whose `cc_slot_end_info` is `None`, firing the second assert. [3](#0-2) 

**Why `__get_rc_sub_slot` fixes do not protect these paths:**
The SEC-614 fix hardened `__get_rc_sub_slot` (called only with `segments[0]` per sub-epoch, line 979) to return `None` instead of asserting. However, `__validate_pospace` and `_get_challenge_block_vdfs` are called for the *sampled* segment (which may be any segment in the sub-epoch, line 977), and their own `assert` statements were not replaced with graceful error returns. [4](#0-3) [5](#0-4) 

**Full node protection (partial):**
`request_validate_wp` wraps `validate_weight_proof` in `try/except Exception`, so the full node itself does not crash — the peer is banned and a `ValueError` is raised. [6](#0-5) 

**Wallet exposure (unprotected):**
`WalletWeightProofHandler.validate_weight_proof` calls `validate_weight_proof_inner` with no surrounding `try/except`. An `AssertionError` from `_validate_sub_epoch_segments` (which runs in the main event loop, not in the executor) propagates directly to the wallet's sync machinery. [7](#0-6) [8](#0-7) 

### Impact Explanation
A malicious peer acting as a full node can send a crafted `WeightProof` to any connecting wallet. The crafted proof passes the `_validate_sub_epoch_summaries` check (which only validates sub-epoch hash chaining and weight totals) and then triggers an `AssertionError` inside `_validate_sub_epoch_segments` during segment validation. Because `WalletWeightProofHandler` has no `try/except` guard, the exception propagates up and crashes the wallet's sync task, permanently preventing the wallet from syncing until restarted — and the attack can be repeated on reconnection. This matches the High impact category: **long-lived inability for wallets to process sync updates under normal network assumptions**.

### Likelihood Explanation
Any node on the Chia network can present itself as a full node and accept wallet connections. The crafted `WeightProof` requires only setting optional fields (`proof_of_space`, `cc_infusion_point`, `cc_ip_vdf_info`) to `None` in a `SubSlotData` entry that is structurally recognized as a challenge block (`cc_slot_end is None`). No cryptographic material needs to be forged; the assert fires before any signature or VDF check is reached. The attack is deterministic and requires no brute force.

### Recommendation
Replace every bare `assert` in `__validate_pospace`, `_get_challenge_block_vdfs`, `__get_cc_sub_slot`, and `_validate_sub_slot_data` that is reachable from peer-supplied data with an explicit `if … is None: log.error(…); return None / return False, []` pattern, consistent with the SEC-614 fix already applied to `__get_rc_sub_slot`. Additionally, wrap `validate_weight_proof_inner` in `WalletWeightProofHandler.validate_weight_proof` with a `try/except Exception` that raises a clean `ValueError`, mirroring the full node's guard in `request_validate_wp`.

### Proof of Concept

1. Construct a `WeightProof` with valid `sub_epochs` (passing `_validate_sub_epoch_summaries`) and at least one `SubEpochChallengeSegment` where `sub_epoch_n > 0`.
2. In that segment, include a `SubSlotData` entry with `cc_slot_end = None` (marking it as a challenge block) and `proof_of_space = None`.
3. Ensure `segments[0]` for that sub-epoch has a structurally valid first slot so `__get_rc_sub_slot` returns a non-`None` value.
4. Send this `WeightProof` to a wallet that has connected to your node.
5. `_validate_segment` calls `__validate_pospace` for the sampled challenge block → `assert sub_slot_data.proof_of_space is not None` fires → `AssertionError` propagates through `validate_weight_proof_inner` → unhandled in `WalletWeightProofHandler.validate_weight_proof` → wallet sync task crashes.

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

**File:** chia/full_node/weight_proof.py (L1073-1075)
```python
    assert sub_slot_data.cc_infusion_point
    assert sub_slot_data.cc_ip_vdf_info
    ip_input = ClassgroupElement.get_default_element()
```

**File:** chia/full_node/weight_proof.py (L1423-1425)
```python
    # validate proof of space
    assert sub_slot_data.proof_of_space is not None

```

**File:** chia/full_node/weight_proof.py (L1446-1465)
```python
def __get_rc_sub_slot(
    constants: ConsensusConstants,
    segment: SubEpochChallengeSegment,
    summaries: list[SubEpochSummary],
    curr_ssi: uint64,
) -> RewardChainSubSlot | None:
    ses = summaries[uint32(segment.sub_epoch_n - 1)]
    # find first challenge in sub epoch
    first_idx = None
    first = None
    for idx, curr in enumerate(segment.sub_slots):
        if curr.cc_slot_end is None:
            first_idx = idx
            first = curr
            break

    if first_idx is None or first is None or first.signage_point_index is None:
        log.error("segment missing challenge block or signage_point_index")
        return None
    idx = first_idx
```

**File:** chia/full_node/weight_proof.py (L1532-1541)
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

**File:** chia/full_node/full_node.py (L1183-1187)
```python
        try:
            validated, fork_point, summaries = await self.weight_proof_handler.validate_weight_proof(response.wp)
        except Exception as e:
            await weight_proof_peer.close(CONSENSUS_ERROR_BAN_SECONDS)
            raise ValueError(f"Weight proof validation threw an error {e}")
```

**File:** chia/wallet/wallet_weight_proof_handler.py (L45-68)
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
        return block_records
```
