### Title
Unvalidated `farmer_reward_address_override` in Harvester `RespondSignatures` Allows Arbitrary Farmer Reward Redirection - (`File: chia/farmer/farmer_api.py`)

### Summary
The farmer's `_process_respond_signatures` method unconditionally accepts `farmer_reward_address_override` from any connected harvester's `RespondSignatures` protocol message without enforcing the CHIP-22 fee-threshold check. A malicious or compromised harvester can redirect 100% of the farmer's block reward (XCH) to any arbitrary puzzle hash.

### Finding Description

The `RespondSignatures` protocol message includes an optional `farmer_reward_address_override` field: [1](#0-0) 

In `_process_respond_signatures`, when this field is non-`None`, the farmer unconditionally replaces its own configured `farmer_target` with the harvester-supplied address: [2](#0-1) 

This `farmer_reward_address` is then passed directly as `farmer_puzzle_hash` in the `DeclareProofOfSpace` message sent to the full node, which determines where the on-chain farmer block reward is paid: [3](#0-2) 

CHIP-22 defines a fee convention where harvesters may take a portion of the farmer reward only when `fee_quality <= applied_fee_threshold`. The farmer does call `notify_farmer_reward_taken_by_harvester_as_fee` when `farmer_reward_address_override` is set in `NewProofOfSpace`: [4](#0-3) 

However, `notify_farmer_reward_taken_by_harvester_as_fee` is **purely informational** — it only logs warnings and returns `None`. It does not return a boolean that would block the override, and the farmer proceeds regardless of whether the fee threshold is valid: [5](#0-4) 

Critically, in `_process_respond_signatures` there is **no fee-threshold check at all** before accepting the override — the path from `RespondSignatures` to `DeclareProofOfSpace` applies the override unconditionally.

The `NewProofOfSpace` message also carries `farmer_reward_address_override`: [6](#0-5) 

### Impact Explanation

**High — Unauthorized payout redirection of XCH farmer block rewards.**

A malicious harvester sets `farmer_reward_address_override` to an attacker-controlled puzzle hash in either `NewProofOfSpace` or `RespondSignatures`. The farmer accepts it without enforcing the fee-quality gate, causing the on-chain `farmer_puzzle_hash` in `DeclareProofOfSpace` to point to the attacker's address. Every block won by that harvester's plots pays the farmer reward to the attacker instead of the legitimate farmer.

### Likelihood Explanation

CHIP-22 explicitly supports third-party harvesters connecting to a farmer. Any harvester operator — not just the farmer's own machines — can send these protocol messages. The attacker does not need keys, admin access, or a cryptographic break; they only need a valid plot and a connection to the farmer. The farmer-harvester protocol is designed to be open to third parties, making this reachable by an unprivileged harvester operator.

### Recommendation

Before accepting `farmer_reward_address_override`, the farmer should enforce the CHIP-22 fee-quality gate rather than merely logging it. Specifically, in `_process_respond_signatures`, compute `fee_quality` and compare it against `fee_info.applied_fee_threshold`; if the threshold is absent or the quality exceeds it, discard the override and use `self.farmer.farmer_target`. The same enforcement should be applied in the `new_proof_of_space` path before proceeding with the override.

### Proof of Concept

1. Operate a third-party harvester with valid plots connected to a victim farmer (permitted by CHIP-22 design).
2. When the farmer sends `RequestSignatures` for a winning proof, respond with a `RespondSignatures` message where `farmer_reward_address_override` is set to an attacker-controlled `bytes32` puzzle hash and `applied_fee_threshold` is set to `0xFFFFFFFF` (maximum, so `fee_quality <= threshold` always holds — but even without `fee_info`, the override is still accepted).
3. The farmer's `_process_respond_signatures` replaces `farmer_reward_address` with the attacker's puzzle hash at line 918 with no further validation.
4. `DeclareProofOfSpace` is sent to the full node with `farmer_puzzle_hash` = attacker's address.
5. The block is farmed and the farmer reward (currently 0.25 XCH) is paid to the attacker's address on-chain, with no recourse for the legitimate farmer. [7](#0-6) [1](#0-0)

### Citations

**File:** chia/protocols/harvester_protocol.py (L66-77)
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

**File:** chia/farmer/farmer_api.py (L128-129)
```python
                if new_proof_of_space.farmer_reward_address_override is not None:
                    self.farmer.notify_farmer_reward_taken_by_harvester_as_fee(sp, new_proof_of_space)
```

**File:** chia/farmer/farmer_api.py (L914-933)
```python
                    include_source_signature_data = response.include_source_signature_data

                    farmer_reward_address = self.farmer.farmer_target
                    if response.farmer_reward_address_override is not None:
                        farmer_reward_address = response.farmer_reward_address_override
                        include_source_signature_data = True

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

**File:** chia/farmer/farmer.py (L920-934)
```python
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
