### Title
Malicious Harvester Can Silently Divert Farmer Block Reward via Unvalidated `farmer_reward_address_override` in `RespondSignatures` — (`File: chia/farmer/farmer_api.py`)

### Summary

A third-party harvester can redirect the farmer's block reward (XCH coinbase) to an arbitrary address by setting `farmer_reward_address_override` in its `RespondSignatures` message. The farmer unconditionally accepts this override without enforcing the CHIP-22 fee-quality threshold, and critically, the fee-quality check is performed only against the `NewProofOfSpace` message — not against the `RespondSignatures` message that actually controls the reward address used in `DeclareProofOfSpace`.

### Finding Description

CHIP-22 introduced `farmer_reward_address_override` as a voluntary fee mechanism: a harvester may redirect the farmer reward to itself when the proof quality exceeds a declared threshold. The farmer is supposed to log a warning and (by convention) disconnect from harvesters that violate the threshold.

The implementation has two separate fields:

1. `NewProofOfSpace.farmer_reward_address_override` — checked and logged in `new_proof_of_space()`.
2. `RespondSignatures.farmer_reward_address_override` — used **without any threshold check** in `_process_respond_signatures()` to set the actual `farmer_reward_address` in `DeclareProofOfSpace`.

In `_process_respond_signatures()`:

```python
farmer_reward_address = self.farmer.farmer_target
if response.farmer_reward_address_override is not None:
    farmer_reward_address = response.farmer_reward_address_override
    include_source_signature_data = True
``` [1](#0-0) 

The fee-quality warning is only triggered by `NewProofOfSpace.farmer_reward_address_override`:

```python
if new_proof_of_space.farmer_reward_address_override is not None:
    self.farmer.notify_farmer_reward_taken_by_harvester_as_fee(sp, new_proof_of_space)
``` [2](#0-1) 

`notify_farmer_reward_taken_by_harvester_as_fee()` only logs — it never rejects the proof or blocks the override from being used: [3](#0-2) 

A malicious harvester can therefore:
- Send `NewProofOfSpace` with `farmer_reward_address_override = None` (no warning logged).
- Later send `RespondSignatures` with `farmer_reward_address_override = attacker_address`.
- The farmer silently uses `attacker_address` as `farmer_puzzle_hash` in `DeclareProofOfSpace`, which the full node embeds in the block's foliage as the farmer reward destination. [4](#0-3) 

The full node accepts `request.farmer_puzzle_hash` from `DeclareProofOfSpace` without restriction: [5](#0-4) 

The `RespondSignatures` protocol message definition confirms the field is optional and unconstrained: [6](#0-5) 

### Impact Explanation

**High — Unauthorized payout redirection.** A malicious third-party harvester can redirect the farmer's block reward (currently 0.25 XCH per block) to any address it controls. The farmer receives no warning when the override is injected only in `RespondSignatures`. The diverted coin is created on-chain and is irrecoverable. This matches the allowed impact: "Bypass of … pool … authorization that enables … payout redirection."

### Likelihood Explanation

Any harvester connected to a farmer can exploit this. Third-party harvesters are explicitly supported (CHIP-22 was added to enable them). The attack requires no special privileges, no key compromise, and no cryptographic break — only a network connection from harvester to farmer. The farmer has no on-chain or protocol-level enforcement to prevent it.

### Recommendation

1. **Enforce threshold before accepting the override**: In `_process_respond_signatures()`, before applying `response.farmer_reward_address_override`, verify that the fee-quality threshold from the corresponding `NewProofOfSpace.fee_info` is satisfied. If the threshold is absent or invalid, ignore the override and use `self.farmer.farmer_target`.
2. **Bind the override across messages**: Store the `farmer_reward_address_override` and `fee_info` from `NewProofOfSpace` keyed by `(plot_identifier, sp_hash)`, and in `_process_respond_signatures()` reject any `RespondSignatures.farmer_reward_address_override` that does not match the stored value or whose threshold was not met.
3. **Reject, not just log**: `notify_farmer_reward_taken_by_harvester_as_fee()` should return a boolean indicating validity; `_process_respond_signatures()` should return `None` (dropping the block) when the threshold is violated.

### Proof of Concept

1. Attacker operates a harvester connected to a victim farmer.
2. Harvester sends `NewProofOfSpace` with `farmer_reward_address_override = None` and `fee_info = None` — no warning is logged.
3. Farmer sends `RequestSignatures` to the harvester.
4. Harvester responds with `RespondSignatures` where `farmer_reward_address_override = attacker_puzzle_hash`.
5. `_process_respond_signatures()` sets `farmer_reward_address = attacker_puzzle_hash` and returns a valid `DeclareProofOfSpace`.
6. The full node creates an unfinished block with `farmer_puzzle_hash = attacker_puzzle_hash` in the foliage.
7. The block is finalized; the farmer reward coin (0.25 XCH) is created at `attacker_puzzle_hash`, permanently lost to the farmer.

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

**File:** chia/farmer/farmer.py (L888-934)
```python
    def notify_farmer_reward_taken_by_harvester_as_fee(
        self, sp: farmer_protocol.NewSignagePoint, proof_of_space: harvester_protocol.NewProofOfSpace
    ) -> None:
        """
        Apply a fee quality convention (see CHIP-22: https://github.com/Chia-Network/chips/pull/88)
        given the proof and signage point. This will be tested against the fee threshold reported
        by the harvester (if any), and logged.
        """
        assert proof_of_space.farmer_reward_address_override is not None

        challenge_str = str(sp.challenge_hash)

        ph_prefix = self.config["network_overrides"]["config"][self.config["selected_network"]]["address_prefix"]
        farmer_reward_puzzle_hash = encode_puzzle_hash(proof_of_space.farmer_reward_address_override, ph_prefix)

        self.log.info(
            f"Farmer reward for challenge '{challenge_str}' "
            + f"taken by harvester for reward address '{farmer_reward_puzzle_hash}'"
        )

        fee_quality = calculate_harvester_fee_quality(proof_of_space.proof.proof, sp.challenge_hash)
        fee_quality_rate = float(fee_quality) / float(0xFFFFFFFF) * 100.0

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

**File:** chia/full_node/full_node_api.py (L1069-1074)
```python
                farmer_ph = request.farmer_puzzle_hash
                if request.proof_of_space.pool_contract_puzzle_hash is not None:
                    pool_target = PoolTarget(request.proof_of_space.pool_contract_puzzle_hash, uint32(0))
                else:
                    assert request.pool_target is not None
                    pool_target = request.pool_target
```

**File:** chia/protocols/harvester_protocol.py (L66-76)
```python
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
