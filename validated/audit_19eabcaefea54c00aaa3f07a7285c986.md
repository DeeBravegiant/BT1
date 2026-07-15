### Title
Malicious Harvester Can Unconditionally Redirect Farmer Block Reward to Arbitrary Address via `RespondSignatures.farmer_reward_address_override` - (File: chia/farmer/farmer_api.py)

### Summary
A malicious harvester connected to a farmer can redirect the farmer's 0.25 XCH block reward to any arbitrary puzzle hash by setting `farmer_reward_address_override` in the `RespondSignatures` protocol message. The farmer's `_process_respond_signatures()` accepts this override unconditionally, with no fee-quality threshold enforcement and no authorization check.

### Finding Description

The CHIP-22 harvester fee convention introduces `farmer_reward_address_override` as an optional field in both `NewProofOfSpace` and `RespondSignatures`. The intended design is that a third-party harvester may redirect the farmer reward only when a deterministic fee-quality threshold is met.

In `FarmerAPI.new_proof_of_space()`, when `NewProofOfSpace.farmer_reward_address_override` is set, the farmer calls `notify_farmer_reward_taken_by_harvester_as_fee()`: [1](#0-0) 

That function performs a fee-quality check but **only logs warnings** — it never blocks the override or returns a failure: [2](#0-1) 

Critically, the actual override that determines the on-chain farmer reward address comes from `RespondSignatures`, processed in `_process_respond_signatures()`: [3](#0-2) 

This override is accepted **unconditionally** — no fee-quality check, no threshold comparison, no authorization. The resulting `farmer_reward_address` is placed directly into `DeclareProofOfSpace`: [4](#0-3) 

The `RespondSignatures` message type carrying this field: [5](#0-4) 

An attacker can also bypass even the advisory check in `new_proof_of_space()` by sending `NewProofOfSpace` **without** `farmer_reward_address_override` (avoiding the log-only check entirely), then injecting the override only in the `RespondSignatures` reply, where no check exists at all.

### Impact Explanation

Every time the farmer wins a block, the 0.25 XCH farmer coinbase reward is created at the puzzle hash specified in `DeclareProofOfSpace.farmer_reward_puzzle_hash`. A malicious harvester sets this to its own address, permanently diverting the farmer's reward. The farmer receives nothing for that block. This is a direct, irreversible XCH loss per won block, matching the **High: payout redirection** impact category.

### Likelihood Explanation

Any harvester that is network-connected to the farmer can exploit this. Third-party harvesters (DrPlotter, GPU harvesters, remote harvesters) are a common deployment pattern. The attacker needs only to be a connected harvester peer — no keys, no admin access, no cryptographic break required. The farmer has no way to detect or prevent the redirect at the protocol level as currently implemented.

### Recommendation

1. In `_process_respond_signatures()`, enforce the fee-quality threshold before accepting `farmer_reward_address_override` from `RespondSignatures` — mirror the check already present for `NewProofOfSpace` and **reject** (return `None`) when the threshold is not met.
2. Optionally, require that `RespondSignatures.farmer_reward_address_override` match the value already declared in the corresponding `NewProofOfSpace`, so the two messages are consistent.
3. Consider making the fee-quality check in `notify_farmer_reward_taken_by_harvester_as_fee()` a hard enforcement (return `False` / abort) rather than a log-only advisory.

### Proof of Concept

1. Attacker operates a harvester connected to a legitimate farmer.
2. Harvester finds a valid proof of space and sends `NewProofOfSpace` to the farmer **without** `farmer_reward_address_override` (no advisory check triggered).
3. Farmer validates the proof and sends `RequestSignatures` back to the harvester.
4. Harvester replies with `RespondSignatures` where `farmer_reward_address_override = attacker_puzzle_hash`.
5. `_process_respond_signatures()` executes line 917–918: `farmer_reward_address = response.farmer_reward_address_override` — no check performed.
6. Farmer broadcasts `DeclareProofOfSpace` with `farmer_reward_puzzle_hash = attacker_puzzle_hash`.
7. Full node creates the 0.25 XCH farmer coinbase coin at the attacker's address. The farmer receives 0 XCH for the won block.

### Citations

**File:** chia/farmer/farmer_api.py (L128-129)
```python
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

**File:** chia/protocols/harvester_protocol.py (L129-139)
```python
@streamable
@dataclass(frozen=True)
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
