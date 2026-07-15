### Title
Malicious Harvester Can Unconditionally Redirect Farmer Block Rewards via Unenforced `farmer_reward_address_override` in `RespondSignatures` - (File: chia/farmer/farmer_api.py)

### Summary

The CHIP-22 third-party harvester protocol introduces a `farmer_reward_address_override` field in `RespondSignatures` that a connected harvester can set to any arbitrary `bytes32` puzzle hash. In `_process_respond_signatures()`, the farmer unconditionally substitutes this value for its own configured `farmer_target` reward address with no cryptographic enforcement, no threshold check, and no consistency check against the `NewProofOfSpace` message. A malicious harvester with valid plots can silently redirect 100% of the farmer's XCH block reward to an attacker-controlled address on every block it wins.

### Finding Description

**Root cause — unconditional override with no enforcement:**

In `chia/farmer/farmer_api.py`, `_process_respond_signatures()` builds the `DeclareProofOfSpace` message that is broadcast to the full node:

```python
farmer_reward_address = self.farmer.farmer_target
if response.farmer_reward_address_override is not None:
    farmer_reward_address = response.farmer_reward_address_override
    include_source_signature_data = True
``` [1](#0-0) 

The `response` is a `RespondSignatures` message received from the harvester peer. The `farmer_reward_address_override` field is a plain `bytes32 | None` in the streamable protocol struct: [2](#0-1) 

There is no validation that:
- The override matches the fee-convention threshold from `NewProofOfSpace.fee_info`
- The override in `RespondSignatures` is consistent with the override in `NewProofOfSpace`
- The override is within any permitted range

**The only "check" is a log warning, not enforcement:**

When `NewProofOfSpace.farmer_reward_address_override` is non-None, the farmer calls `notify_farmer_reward_taken_by_harvester_as_fee()`, which computes the fee quality and emits a `log.warning` if the threshold is invalid — but does **not** block the override or abort the block-making flow: [3](#0-2) [4](#0-3) 

**Silent attack path — bypassing even the log warning:**

The fee-convention logging is only triggered by `NewProofOfSpace.farmer_reward_address_override`. The `RespondSignatures.farmer_reward_address_override` is a separate field that is consumed silently. A malicious harvester can:

1. Send `NewProofOfSpace` with `farmer_reward_address_override=None` → no fee-convention log is emitted.
2. Farmer sends `RequestSignatures` back to the harvester.
3. Harvester responds with `RespondSignatures` where `farmer_reward_address_override = attacker_puzzle_hash`.
4. `_process_respond_signatures()` substitutes `attacker_puzzle_hash` for `self.farmer.farmer_target` with no warning.
5. `DeclareProofOfSpace` is broadcast to the full node with the attacker's puzzle hash as the farmer reward address.
6. The full node validates the BLS signature (which the farmer signed over the attacker's address) and accepts the block.
7. The farmer reward coin is created at the attacker's puzzle hash. [5](#0-4) 

### Impact Explanation

**High — unauthorized payout redirection of XCH block rewards.**

Every block the farmer wins while connected to the malicious harvester has its farmer reward (currently 0.25 XCH per block) permanently redirected to the attacker's address. The farmer receives nothing. The full node accepts the block because the BLS signature is valid — the farmer unknowingly signed a `FoliageBlockData` containing the attacker's `farmer_reward_puzzle_hash`. There is no on-chain or protocol-level mechanism to detect or reverse this.

This matches the allowed High impact: *"Bypass of … authorization that enables … payout redirection … with direct security impact."*

### Likelihood Explanation

Any party that can operate a harvester with valid plots and connect to a victim farmer can execute this attack. Third-party harvesters are an explicitly supported use case (CHIP-22), so farmers routinely connect to harvesters they do not fully control. The attack requires no leaked keys, no admin access, and no cryptographic break — only a network connection and valid plots. The silent variant (no `farmer_reward_address_override` in `NewProofOfSpace`) produces no log warning, making detection difficult.

### Recommendation

1. **Enforce the fee threshold as a hard limit, not a log warning.** If `farmer_reward_address_override` is set in `RespondSignatures` and the fee quality does not satisfy the declared threshold, abort `_process_respond_signatures()` and return `None` instead of proceeding.
2. **Require consistency between `NewProofOfSpace` and `RespondSignatures`.** If `farmer_reward_address_override` appears in `RespondSignatures` but was absent in the corresponding `NewProofOfSpace`, treat it as a protocol violation and disconnect the harvester.
3. **Optionally allow farmers to disable the override entirely** via a config flag, so operators who do not use third-party harvesters cannot be affected.

### Proof of Concept

```
Attacker operates a harvester with valid plots.
Connects to victim farmer.

On finding a valid proof of space:
  1. Send NewProofOfSpace(farmer_reward_address_override=None, ...)
     → Farmer logs nothing about fee convention.
  2. Farmer sends RequestSignatures back.
  3. Harvester replies with RespondSignatures(
         farmer_reward_address_override=ATTACKER_PUZZLE_HASH,
         message_signatures=[valid_harvester_half_sig],
         ...
     )
  4. Farmer executes _process_respond_signatures():
       farmer_reward_address = self.farmer.farmer_target   # victim's address
       if response.farmer_reward_address_override is not None:
           farmer_reward_address = ATTACKER_PUZZLE_HASH   # silently replaced
  5. Farmer broadcasts DeclareProofOfSpace with farmer_reward_address=ATTACKER_PUZZLE_HASH.
  6. Full node accepts block; farmer reward coin created at ATTACKER_PUZZLE_HASH.
  7. Victim farmer receives 0 XCH for the block.
``` [1](#0-0) [6](#0-5)

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
