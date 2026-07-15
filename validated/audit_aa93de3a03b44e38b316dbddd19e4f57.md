### Title
Malicious Third-Party Harvester Can Unconditionally Redirect Farmer Block Rewards via Unenforced `farmer_reward_address_override` - (File: chia/farmer/farmer_api.py)

### Summary

The CHIP-22 third-party harvester protocol allows a connected harvester to set `farmer_reward_address_override` in `NewProofOfSpace` and `RespondSignatures` messages to redirect the farmer's block reward to an arbitrary puzzle hash. The farmer's only response to a fee-quality violation is to emit a log warning; it never blocks the override, never rejects the block, and never disconnects the harvester. A malicious third-party harvester can therefore redirect 100% of the farmer's XCH block rewards to an attacker-controlled address on every won block, with no on-node enforcement preventing it.

### Finding Description

**Root cause — two unenforced override acceptance points:**

**Point 1 — `new_proof_of_space` in `FarmerAPI`**

When a harvester sends `NewProofOfSpace` with `farmer_reward_address_override != None`, the farmer calls `notify_farmer_reward_taken_by_harvester_as_fee`, which only logs a warning and returns `None`. Execution continues unconditionally into the block-making flow regardless of whether the fee quality threshold was violated. [1](#0-0) 

```python
if new_proof_of_space.farmer_reward_address_override is not None:
    self.farmer.notify_farmer_reward_taken_by_harvester_as_fee(sp, new_proof_of_space)
# execution continues — no early return, no rejection
```

`notify_farmer_reward_taken_by_harvester_as_fee` emits `log.warning(...)` on threshold violation but has no return value and no side-effect that stops processing: [2](#0-1) 

**Point 2 — `_process_respond_signatures` in `FarmerAPI`**

When the harvester's `RespondSignatures` message carries `farmer_reward_address_override`, the farmer replaces its own `farmer_target` with the harvester-supplied address with **zero validation** — no fee-quality check, no threshold comparison, no logging: [3](#0-2) 

```python
farmer_reward_address = self.farmer.farmer_target
if response.farmer_reward_address_override is not None:
    farmer_reward_address = response.farmer_reward_address_override  # accepted unconditionally
    include_source_signature_data = True
```

The resulting `DeclareProofOfSpace` is broadcast to all full nodes with the attacker's puzzle hash as `farmer_reward_address`: [4](#0-3) 

**Protocol message definitions** confirm both fields are attacker-controlled with no cryptographic binding to the farmer's key: [5](#0-4) 

**Exploit path:**

1. Attacker operates a third-party harvester and connects it to the victim farmer (legitimate, unprivileged role under CHIP-22).
2. On every `NewSignagePointHarvester`, the harvester returns `NewProofOfSpace` with `farmer_reward_address_override` = attacker's puzzle hash and `fee_info.applied_fee_threshold = 0xFFFFFFFF` (always passes the logged check) or `fee_info = None`.
3. The farmer logs a warning (or nothing) and proceeds to request signatures.
4. The harvester returns `RespondSignatures` with `farmer_reward_address_override` = attacker's puzzle hash.
5. The farmer builds and broadcasts `DeclareProofOfSpace` with the attacker's address as the farmer reward destination.
6. The full node accepts the block; the farmer reward coin (currently 0.25 XCH per block) is created at the attacker's puzzle hash.

### Impact Explanation

Every block won by the farmer while the malicious harvester is connected pays the farmer reward to the attacker's address. The farmer's legitimate `farmer_target` is silently bypassed. This is a direct, permanent, per-block theft of XCH with no on-chain or on-node mechanism to reverse it. Impact category: **High — unauthorized payout redirection of XCH block rewards**.

### Likelihood Explanation

Any operator who connects a third-party harvester (the explicit use case of CHIP-22) is exposed. The attacker needs only to be a connected harvester peer — no keys, no admin access, no cryptographic break required. The farmer has no automatic disconnection logic for harvesters that violate the fee convention.

### Recommendation

- In `new_proof_of_space`, after calling `notify_farmer_reward_taken_by_harvester_as_fee`, enforce the fee quality check: if `fee_quality > fee_threshold` (or `fee_info is None`), return early and do not proceed with the block-making flow for that proof.
- In `_process_respond_signatures`, perform the same fee-quality validation before accepting `farmer_reward_address_override` from `RespondSignatures`. If the override was not announced in the corresponding `NewProofOfSpace`, reject it entirely.
- Consider adding automatic disconnection or rate-limiting for harvesters that repeatedly violate the fee convention.

### Proof of Concept

A malicious harvester intercepts the normal flow and injects `farmer_reward_address_override` into both messages:

```python
# In NewProofOfSpace handler (harvester side):
new_pos = dataclasses.replace(
    original_pos,
    farmer_reward_address_override=attacker_puzzle_hash,
    fee_info=ProofOfSpaceFeeInfo(applied_fee_threshold=uint32(0xFFFFFFFF)),
)
# Farmer calls notify_farmer_reward_taken_by_harvester_as_fee → logs "Fee threshold passed" → continues

# In RespondSignatures handler (harvester side):
response = dataclasses.replace(
    original_response,
    farmer_reward_address_override=attacker_puzzle_hash,
)
# Farmer _process_respond_signatures line 917-918: unconditionally sets farmer_reward_address = attacker_puzzle_hash
# DeclareProofOfSpace broadcast with attacker address → block reward goes to attacker
```

This is confirmed by the existing test infrastructure in `test_third_party_harvesters.py` which demonstrates the override is respected end-to-end: [6](#0-5)

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

**File:** chia/_tests/farmer_harvester/test_third_party_harvesters.py (L134-139)
```python
        # Inject overridden farmer reward address
        response: RespondSignatures = dataclasses.replace(
            RespondSignatures.from_bytes(result_msg.data), farmer_reward_address_override=farmer_reward_address
        )

        return make_msg(ProtocolMessageTypes.respond_signatures, response)
```
