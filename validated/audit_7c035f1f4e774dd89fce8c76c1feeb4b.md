### Title
Malicious Third-Party Harvester Can Unconditionally Redirect Farmer Block Rewards via Unenforced `farmer_reward_address_override` — (`chia/farmer/farmer_api.py`)

### Summary

The CHIP-22 harvester fee convention allows a connected harvester to set `farmer_reward_address_override` in `NewProofOfSpace` and `RespondSignatures` messages. The farmer's only guard is a log-only warning in `notify_farmer_reward_taken_by_harvester_as_fee`. There is no enforcement: the farmer unconditionally uses the harvester-supplied address as the block reward destination. A malicious third-party harvester can redirect 100% of the farmer's XCH block rewards to an arbitrary address with no on-chain or protocol-level check blocking it.

### Finding Description

When a harvester wins a block, it sends `NewProofOfSpace` to the farmer. If `farmer_reward_address_override` is non-`None`, the farmer calls `notify_farmer_reward_taken_by_harvester_as_fee`, which checks a "fee quality" value against `applied_fee_threshold` — but only logs a warning on mismatch and never rejects the message or aborts the block-making flow. [1](#0-0) 

The farmer then sends `RequestSignatures` to the harvester. When the harvester responds with `RespondSignatures`, the farmer again reads `farmer_reward_address_override` and unconditionally substitutes it for `self.farmer.farmer_target`: [2](#0-1) 

This overridden address is placed directly into `DeclareProofOfSpace.farmer_reward_puzzle_hash` and submitted to the full node, permanently directing the coinbase reward to the attacker's address. [3](#0-2) 

The fee-quality check in `notify_farmer_reward_taken_by_harvester_as_fee` is purely advisory: [4](#0-3) 

A malicious harvester can trivially bypass even the log warning by setting `applied_fee_threshold = 0xFFFFFFFF` (max `uint32`), which always satisfies `fee_quality <= fee_threshold`, producing only an INFO log entry — not a rejection. [5](#0-4) 

The `fee_info` field is optional (`fee_info: ProofOfSpaceFeeInfo | None`). Omitting it entirely also only produces a warning log, not a block: [6](#0-5) 

The `farmer_reward_address_override` field in `RespondSignatures` is also not validated against the one in `NewProofOfSpace`, nor against any farmer-configured allowlist: [7](#0-6) 

### Impact Explanation

Every block won by plots managed by the malicious harvester will have its full 0.25 XCH farmer reward (plus any transaction fees) sent to the attacker's puzzle hash instead of the farmer's configured address. This is a permanent, on-chain, irreversible diversion of XCH. The farmer has no in-protocol recourse once the block is submitted. This matches the **High** impact category: "Bypass of … authorization that enables … payout redirection."

### Likelihood Explanation

The attack requires a farmer to connect to a malicious third-party harvester — the exact scenario CHIP-22 was designed to support. Third-party harvesters are a documented, supported use case. The attacker needs no keys, no admin access, and no cryptographic break. They only need to be an accepted harvester peer. The attack is silent (no error, only an INFO log at best) and takes effect on the very first block won.

### Recommendation

The farmer must enforce the fee-quality check, not merely log it. Specifically:

1. If `farmer_reward_address_override` is set in `RespondSignatures` but was **not** set in the corresponding `NewProofOfSpace`, reject the response.
2. If `fee_info` is absent when `farmer_reward_address_override` is present, reject the response.
3. If `fee_quality > applied_fee_threshold`, reject the response (do not proceed with `DeclareProofOfSpace`).
4. Optionally, allow farmers to configure a maximum fraction of the reward that any harvester may redirect, enforced in code. [8](#0-7) 

### Proof of Concept

A malicious harvester intercepts the normal `RespondSignatures` path and injects:

```python
# In harvester's RespondSignatures handler:
response = dataclasses.replace(
    original_response,
    farmer_reward_address_override=attacker_puzzle_hash,  # arbitrary address
)
# In NewProofOfSpace:
new_pos = dataclasses.replace(
    original_pos,
    farmer_reward_address_override=attacker_puzzle_hash,
    fee_info=ProofOfSpaceFeeInfo(applied_fee_threshold=uint32(0xFFFFFFFF)),  # always passes
)
```

The farmer receives `NewProofOfSpace`, calls `notify_farmer_reward_taken_by_harvester_as_fee`, logs "Fee threshold passed" (INFO level), proceeds to request signatures, receives the tampered `RespondSignatures`, and emits `DeclareProofOfSpace` with `farmer_reward_puzzle_hash = attacker_puzzle_hash`. The block reward is permanently lost to the farmer.

This is confirmed by the existing test infrastructure which demonstrates the override is accepted end-to-end: [9](#0-8)

### Citations

**File:** chia/farmer/farmer_api.py (L128-129)
```python
                if new_proof_of_space.farmer_reward_address_override is not None:
                    self.farmer.notify_farmer_reward_taken_by_harvester_as_fee(sp, new_proof_of_space)
```

**File:** chia/farmer/farmer_api.py (L914-919)
```python
                    include_source_signature_data = response.include_source_signature_data

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

**File:** chia/farmer/farmer.py (L911-928)
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
```

**File:** chia/farmer/farmer.py (L929-934)
```python
        else:
            self.log.warning(
                "Harvester illegitimately took reward by failing to provide its fee rate "
                + f"for challenge '{challenge_str}'. "
                + f"Fee quality was {fee_quality_rate:.3f}% ({fee_quality} or 0x{fee_quality:08x})"
            )
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

**File:** chia/_tests/farmer_harvester/test_third_party_harvesters.py (L134-139)
```python
        # Inject overridden farmer reward address
        response: RespondSignatures = dataclasses.replace(
            RespondSignatures.from_bytes(result_msg.data), farmer_reward_address_override=farmer_reward_address
        )

        return make_msg(ProtocolMessageTypes.respond_signatures, response)
```
