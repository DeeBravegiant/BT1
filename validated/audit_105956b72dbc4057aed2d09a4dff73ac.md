### Title
Unenforced CHIP-22 Fee-Threshold Allows Third-Party Harvester to Unconditionally Divert Farmer Reward - (`File: chia/farmer/farmer_api.py`, `chia/farmer/farmer.py`)

---

### Summary

The CHIP-22 third-party harvester fee convention allows a harvester to redirect the farmer's block reward (1/8 of block reward, ~0.25 XCH) to itself as a "fee" — but only when a deterministic `fee_quality` value derived from the proof and challenge falls at or below a declared `applied_fee_threshold`. The farmer's enforcement of this threshold is limited to logging a warning. The farmer never rejects the proof or blocks the reward redirect when the threshold check fails. A malicious third-party harvester can unconditionally steal the farmer reward on every block it finds, regardless of whether the fee quality condition is met.

---

### Finding Description

**Step 1 — Harvester sends `NewProofOfSpace` with `farmer_reward_address_override`.**

In `FarmerAPI.new_proof_of_space` (`chia/farmer/farmer_api.py`), when the harvester sets `farmer_reward_address_override` to its own address, the farmer calls `notify_farmer_reward_taken_by_harvester_as_fee`: [1](#0-0) 

**Step 2 — Fee threshold check is logging-only, never enforced.**

Inside `notify_farmer_reward_taken_by_harvester_as_fee` (`chia/farmer/farmer.py`), the farmer computes `fee_quality` from the proof and challenge, then compares it to the harvester-supplied `applied_fee_threshold`. If the check fails (harvester is not entitled to the fee), the farmer only emits a warning log — it does **not** return an error, reject the proof, or stop processing: [2](#0-1) 

**Step 3 — Processing continues unconditionally after the warning.**

After `notify_farmer_reward_taken_by_harvester_as_fee` returns (void), the farmer stores the proof and sends `RequestSignatures` to the harvester regardless of whether the fee threshold was met: [3](#0-2) 

**Step 4 — `RespondSignatures.farmer_reward_address_override` is accepted with zero fee-threshold validation.**

In `_process_respond_signatures`, the harvester's `farmer_reward_address_override` from the `RespondSignatures` message is applied directly to the `DeclareProofOfSpace` sent to the full node, with no fee threshold check whatsoever: [4](#0-3) 

The `RespondSignatures` protocol message carries `farmer_reward_address_override` as an optional field: [5](#0-4) 

The resulting `DeclareProofOfSpace` is broadcast to all full nodes with the harvester's address as `farmer_reward_address`, causing the farmer reward coin to be created at the harvester's puzzle hash: [6](#0-5) 

---

### Impact Explanation

A malicious third-party harvester (an unprivileged network participant that the farmer has connected to under CHIP-22) can set `farmer_reward_address_override` in both `NewProofOfSpace` and `RespondSignatures` to its own puzzle hash on every block it finds. The farmer reward (~0.25 XCH per block at current schedule) is permanently redirected to the attacker. The farmer receives nothing for blocks found by that harvester. This is unauthorized reward diversion of XCH by an unprivileged actor with no on-chain recourse — the block is valid and confirmed by consensus.

---

### Likelihood Explanation

Any operator of a third-party harvester service (the exact use-case CHIP-22 was designed for) can exploit this. The farmer must connect to the harvester to use its storage capacity, and once connected, the harvester controls `farmer_reward_address_override` in its responses. The farmer has no protocol-level mechanism to reject the override when the fee threshold is not met. The only mitigation is disconnecting the harvester after the fact, but the reward is already lost.

---

### Recommendation

In `FarmerAPI.new_proof_of_space`, after calling `notify_farmer_reward_taken_by_harvester_as_fee`, enforce the fee threshold: if `fee_info` is absent or `fee_quality > applied_fee_threshold`, log the warning **and return `None`** to abort processing of that proof. Additionally, in `_process_respond_signatures`, verify that the `farmer_reward_address_override` in `RespondSignatures` matches the one declared in the original `NewProofOfSpace` (stored alongside the proof), and that the fee threshold was already validated and passed before accepting the override.

---

### Proof of Concept

1. Operator runs a third-party harvester that, for every proof found, constructs `NewProofOfSpace` with `farmer_reward_address_override = attacker_puzzle_hash` and `fee_info = ProofOfSpaceFeeInfo(applied_fee_threshold=0xFFFFFFFF)` (maximum threshold, always "passes" the check trivially).
2. Farmer receives the message, calls `notify_farmer_reward_taken_by_harvester_as_fee`, logs "Fee threshold passed", and continues.
3. Farmer sends `RequestSignatures` to the harvester.
4. Harvester returns `RespondSignatures` with `farmer_reward_address_override = attacker_puzzle_hash`.
5. Farmer calls `_process_respond_signatures`, sets `farmer_reward_address = attacker_puzzle_hash` at line 918, and broadcasts `DeclareProofOfSpace` to the full node.
6. Full node creates the farmer reward coin at `attacker_puzzle_hash`. The farmer receives 0 XCH for the block.

Alternatively, the harvester can omit `fee_info` entirely (`fee_info=None`). The farmer logs a warning ("Harvester illegitimately took reward by failing to provide its fee rate") but still proceeds identically — the reward is still diverted. [7](#0-6) [8](#0-7)

### Citations

**File:** chia/farmer/farmer_api.py (L127-129)
```python
            if required_iters < calculate_sp_interval_iters(self.farmer.constants, sp.sub_slot_iters):
                if new_proof_of_space.farmer_reward_address_override is not None:
                    self.farmer.notify_farmer_reward_taken_by_harvester_as_fee(sp, new_proof_of_space)
```

**File:** chia/farmer/farmer_api.py (L170-187)
```python
                if new_proof_of_space.sp_hash not in self.farmer.proofs_of_space:
                    self.farmer.proofs_of_space[new_proof_of_space.sp_hash] = []
                self.farmer.proofs_of_space[new_proof_of_space.sp_hash].append(
                    (
                        new_proof_of_space.plot_identifier,
                        new_proof_of_space.proof,
                    )
                )
                self.farmer.cache_add_time[new_proof_of_space.sp_hash] = uint64(time.time())
                self.farmer.quality_str_to_identifiers[computed_quality_string] = (
                    new_proof_of_space.plot_identifier,
                    new_proof_of_space.challenge_hash,
                    new_proof_of_space.sp_hash,
                    peer.peer_node_id,
                )
                self.farmer.cache_add_time[computed_quality_string] = uint64(time.time())

                await peer.send_message(make_msg(ProtocolMessageTypes.request_signatures, request))
```

**File:** chia/farmer/farmer_api.py (L916-919)
```python
                    farmer_reward_address = self.farmer.farmer_target
                    if response.farmer_reward_address_override is not None:
                        farmer_reward_address = response.farmer_reward_address_override
                        include_source_signature_data = True
```

**File:** chia/farmer/farmer_api.py (L921-933)
```python
                    return farmer_protocol.DeclareProofOfSpace(
                        response.challenge_hash,
                        challenge_chain_sp,
                        signage_point_index,
                        reward_chain_sp,
                        pospace,
                        agg_sig_cc_sp,
                        agg_sig_rc_sp,
                        farmer_reward_address,
                        pool_target,
                        pool_target_signature,
                        include_signature_source_data=include_source_signature_data,
                    )
```

**File:** chia/farmer/farmer.py (L911-934)
```python
        if proof_of_space.fee_info is not None:
            fee_threshold = proof_of_space.fee_info.applied_fee_threshold
            fee_threshold_rate = float(fee_threshold) / float(0xFFFFFFFF) * 100.0

            if fee_quality <= fee_threshold:
                self.log.info(
                    f"Fee threshold passed for challenge '{challenge_str}': "
                    + f"{fee_quality_rate:.3f}%/{fee_threshold_rate:.3f}% ({fee_quality}/{fee_threshold})"
                )
            else:
                self.log.warning(
                    f"Invalid fee threshold for challenge '{challenge_str}': "
                    + f"{fee_quality_rate:.3f}%/{fee_threshold_rate:.3f}% ({fee_quality}/{fee_threshold})"
                )
                self.log.warning(
                    "Harvester illegitimately took a fee reward that "
                    + "did not belong to it or it incorrectly applied the fee convention."
                )
        else:
            self.log.warning(
                "Harvester illegitimately took reward by failing to provide its fee rate "
                + f"for challenge '{challenge_str}'. "
                + f"Fee quality was {fee_quality_rate:.3f}% ({fee_quality} or 0x{fee_quality:08x})"
            )
```

**File:** chia/protocols/harvester_protocol.py (L62-76)
```python
class ProofOfSpaceFeeInfo(Streamable):
    applied_fee_threshold: uint32


@streamable
@dataclass(frozen=True)
class NewProofOfSpace(Streamable):
    challenge_hash: bytes32
    sp_hash: bytes32
    plot_identifier: str
    proof: ProofOfSpace
    signage_point_index: uint8
    include_source_signature_data: bool
    farmer_reward_address_override: bytes32 | None
    fee_info: ProofOfSpaceFeeInfo | None
```

**File:** chia/protocols/harvester_protocol.py (L131-139)
```python
class RespondSignatures(Streamable):
    plot_identifier: str
    challenge_hash: bytes32
    sp_hash: bytes32
    local_pk: G1Element
    farmer_pk: G1Element
    message_signatures: list[tuple[bytes32, G2Element]]
    include_source_signature_data: bool
    farmer_reward_address_override: bytes32 | None
```
