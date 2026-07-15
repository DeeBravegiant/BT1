### Title
Malicious Third-Party Harvester Can Unconditionally Redirect Farmer Block Rewards to Arbitrary Address — (`File: chia/farmer/farmer_api.py`)

### Summary

The Chia farmer unconditionally accepts a `farmer_reward_address_override` field from a connected harvester's `RespondSignatures` message and uses it as the on-chain farmer reward destination in `DeclareProofOfSpace`, with no enforcement of the CHIP-22 fee-quality convention and no validation that the address belongs to the harvester. A malicious third-party harvester can redirect the farmer's XCH block reward to any arbitrary puzzle hash.

### Finding Description

CHIP-22 introduced `farmer_reward_address_override` as an optional field in both `NewProofOfSpace` and `RespondSignatures` to allow third-party harvesters to take a fee by redirecting the farmer reward. The fee-quality convention (computed via `calculate_harvester_fee_quality`) is supposed to govern when this is legitimate.

**Root cause — `_process_respond_signatures` in `chia/farmer/farmer_api.py`:**

```python
farmer_reward_address = self.farmer.farmer_target
if response.farmer_reward_address_override is not None:
    farmer_reward_address = response.farmer_reward_address_override   # ← no validation
    include_source_signature_data = True
``` [1](#0-0) 

The farmer blindly substitutes any puzzle hash the harvester supplies into the `DeclareProofOfSpace` message that is broadcast to the full node, which then mints the farmer-reward coin to that address. [2](#0-1) 

The only check that exists is `notify_farmer_reward_taken_by_harvester_as_fee`, called from `new_proof_of_space` when the harvester first announces the proof:

```python
if new_proof_of_space.farmer_reward_address_override is not None:
    self.farmer.notify_farmer_reward_taken_by_harvester_as_fee(sp, new_proof_of_space)
``` [3](#0-2) 

That function only **logs** a warning when the fee-quality threshold is violated — it never rejects the proof or the override: [4](#0-3) 

Furthermore, `_process_respond_signatures` reads `farmer_reward_address_override` from the **`RespondSignatures`** message, not from `NewProofOfSpace`. The harvester can therefore supply a completely different (or absent) override in `NewProofOfSpace` to avoid triggering the log check, and then inject the real malicious address in `RespondSignatures`, where **no fee-quality check is performed at all**.

The `RespondSignatures` protocol message explicitly carries this field: [5](#0-4) 

### Impact Explanation

Every time the farmer wins a block while connected to a malicious third-party harvester, the farmer-reward coin (currently 0.25 XCH per block) is minted to the harvester's chosen puzzle hash instead of the farmer's configured `xch_target_address`. The harvester can redirect to its own address, a burn address, or any other address. This is an unauthorized, permanent diversion of XCH block rewards with no on-chain recourse.

### Likelihood Explanation

Any farmer who connects to a third-party harvester service (the explicit use-case of CHIP-22) is exposed. The harvester is a network peer the farmer has chosen to connect to, but the farmer has not consented to unconditional reward redirection. The attack requires no leaked keys, no admin access, and no cryptographic break — only a TCP connection from the harvester to the farmer.

### Recommendation

The farmer should enforce the fee-quality convention as a hard gate, not merely a log warning. Before accepting `farmer_reward_address_override` from either `NewProofOfSpace` or `RespondSignatures`, the farmer must verify that `calculate_harvester_fee_quality(proof, challenge) <= applied_fee_threshold` and that `fee_info` is present. If the check fails, the override must be discarded and the farmer's own `farmer_target` used instead.

### Proof of Concept

1. Farmer connects to a malicious third-party harvester.
2. Harvester finds a valid proof of space and sends `NewProofOfSpace` with `farmer_reward_address_override=None` and `fee_info=None` (bypassing the log check entirely).
3. Farmer sends `RequestSignatures` to the harvester.
4. Harvester responds with `RespondSignatures` where `farmer_reward_address_override` is set to the attacker's puzzle hash.
5. `_process_respond_signatures` substitutes the attacker's address as `farmer_reward_address` with no validation.
6. `DeclareProofOfSpace` is broadcast to the full node with the attacker's puzzle hash as the farmer reward destination.
7. The block is accepted; the farmer-reward coin is minted to the attacker's address.

The farmer's own `farmer_target` is never used for that block. The farmer receives no XCH.

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
