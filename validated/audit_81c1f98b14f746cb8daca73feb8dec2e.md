Based on my reading of the actual production code, this is a real vulnerability. Let me trace the exact path.

---

### Title
Farmer Unconditionally Accepts `farmer_reward_address_override` Without Enforcing `fee_info.applied_fee_threshold` — (`chia/farmer/farmer_api.py`)

### Summary

A malicious third-party harvester (CHIP-22 model) can redirect 100% of a farmer's block rewards to an attacker-controlled address by sending a `NewProofOfSpace` message with `farmer_reward_address_override` set to the attacker's puzzle hash. The farmer calls `notify_farmer_reward_taken_by_harvester_as_fee` (which only logs) and never validates that the proof quality satisfies `fee_info.applied_fee_threshold`. The override is then unconditionally applied in `_process_respond_signatures`.

### Finding Description

**Step 1 — `new_proof_of_space` accepts the override without enforcement:**

In `farmer_api.py`, when a `NewProofOfSpace` arrives with `farmer_reward_address_override is not None`, the farmer only calls `notify_farmer_reward_taken_by_harvester_as_fee` and continues processing: [1](#0-0) 

There is no call to `calculate_harvester_fee_quality`, no comparison against `applied_fee_threshold`, and no `return None` to block the override. Execution falls through to `RequestSignatures` being sent to the harvester.

**Step 2 — `_process_respond_signatures` applies the override unconditionally:**

When the harvester's `RespondSignatures` arrives (also carrying `farmer_reward_address_override`), the farmer replaces its own `farmer_target` with the harvester-supplied address with no validation: [2](#0-1) 

The `fee_info` from the original `NewProofOfSpace` is never consulted here. The `DeclareProofOfSpace` is then built with `farmer_reward_address = attacker_ph`: [3](#0-2) 

**Step 3 — `fee_info` is a dead field for enforcement purposes:**

`ProofOfSpaceFeeInfo.applied_fee_threshold` is defined in the protocol: [4](#0-3) 

But the farmer never reads it to gate the override. Setting `applied_fee_threshold=0xFFFFFFFF` (uint32 max) means the threshold is trivially satisfied for any proof quality, yet even this check is never performed by the farmer.

### Impact Explanation

A malicious third-party harvester (explicitly supported by CHIP-22, as evidenced by `rc_block_unfinished` and `message_data` fields in `RequestSignatures`) can redirect **every** won block's farmer reward (1.75 XCH per block) to an attacker-controlled address. The farmer's own `farmer_target` is silently overridden. This is an unconditional, per-block reward diversion with no farmer-side enforcement.

### Likelihood Explanation

Any farmer using a third-party harvester service (DrPlotter and similar are explicitly referenced in the codebase) is exposed. The attacker needs only to control the harvester binary or the harvester-to-farmer TCP connection. No key material is required. The attack is silent — the farmer sees only a log warning, not a block or error.

### Recommendation

In `new_proof_of_space`, before proceeding past the `farmer_reward_address_override` check, the farmer must:
1. Compute `calculate_harvester_fee_quality(proof, challenge_hash)`.
2. Verify `fee_info is not None` and `fee_quality <= fee_info.applied_fee_threshold`.
3. Return `None` (drop the proof) if the threshold is not satisfied.

The farmer must also store the validated `fee_info` alongside the proof in `proofs_of_space` and re-validate it in `_process_respond_signatures` before applying `response.farmer_reward_address_override`.

### Proof of Concept

Integration test sketch:
1. Farmer has a valid SP.
2. Malicious harvester sends `NewProofOfSpace` with `farmer_reward_address_override=attacker_ph` and `fee_info=ProofOfSpaceFeeInfo(applied_fee_threshold=uint32(0))` (impossible to satisfy — quality can never be ≤ 0).
3. Farmer calls `notify_farmer_reward_taken_by_harvester_as_fee` (logs warning), does **not** return.
4. Farmer sends `RequestSignatures`; harvester responds with `RespondSignatures(farmer_reward_address_override=attacker_ph)`.
5. `_process_respond_signatures` sets `farmer_reward_address = attacker_ph`.
6. `DeclareProofOfSpace.farmer_reward_address == attacker_ph` — assert fails, proving the override was accepted despite an impossible threshold.

### Citations

**File:** chia/farmer/farmer_api.py (L127-130)
```python
            if required_iters < calculate_sp_interval_iters(self.farmer.constants, sp.sub_slot_iters):
                if new_proof_of_space.farmer_reward_address_override is not None:
                    self.farmer.notify_farmer_reward_taken_by_harvester_as_fee(sp, new_proof_of_space)

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

**File:** chia/protocols/harvester_protocol.py (L62-64)
```python
class ProofOfSpaceFeeInfo(Streamable):
    applied_fee_threshold: uint32

```
