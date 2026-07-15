Now I have a complete picture of the vulnerability. Let me verify the exact lines in the farmer API.

### Title
Malicious Harvester Can Unconditionally Redirect Farmer Block Reward via Unenforced `farmer_reward_address_override` in `RespondSignatures` — (`File: chia/farmer/farmer_api.py`)

### Summary

The CHIP-22 fee convention allows a third-party harvester to redirect the farmer block reward (1/8 of block reward, ~0.25 XCH) to itself as a fee, gated by a probabilistic fee-quality check. However, the fee-quality check in `Farmer.notify_farmer_reward_taken_by_harvester_as_fee` is **advisory-only** (log-only, no enforcement), and the actual reward-address override is applied from `RespondSignatures.farmer_reward_address_override` in `_process_respond_signatures` **without any validation**. A malicious harvester can bypass the fee-quality check entirely and redirect the farmer reward to an arbitrary address on every block it finds.

### Finding Description

The farmer-harvester block-production flow has two distinct protocol messages that carry a `farmer_reward_address_override` field:

1. **`NewProofOfSpace`** (`chia/protocols/harvester_protocol.py` lines 68–76): sent by the harvester to the farmer when a valid proof is found. Contains `farmer_reward_address_override: bytes32 | None` and `fee_info: ProofOfSpaceFeeInfo | None`.

2. **`RespondSignatures`** (`chia/protocols/harvester_protocol.py` lines 131–139): sent by the harvester back to the farmer after signing. Also contains `farmer_reward_address_override: bytes32 | None`.

**Step 1 — Fee-quality check is log-only and skippable.**

In `FarmerAPI.new_proof_of_space` (`chia/farmer/farmer_api.py` lines 128–129):

```python
if new_proof_of_space.farmer_reward_address_override is not None:
    self.farmer.notify_farmer_reward_taken_by_harvester_as_fee(sp, new_proof_of_space)
```

`notify_farmer_reward_taken_by_harvester_as_fee` (`chia/farmer/farmer.py` lines 888–934) computes `fee_quality = calculate_harvester_fee_quality(proof, challenge)` and compares it to `fee_info.applied_fee_threshold`. When the check fails it only emits `self.log.warning(...)` — it does **not** return a value, raise an exception, or set any flag that would block the subsequent override. The function's own docstring says "This will be tested against the fee threshold reported by the harvester (if any), **and logged**."

**Step 2 — The fee-quality check is not triggered at all if `NewProofOfSpace.farmer_reward_address_override` is `None`.**

A harvester can send `NewProofOfSpace` with `farmer_reward_address_override=None` and `fee_info=None`. The `if` guard at line 128 is `False`, so `notify_farmer_reward_taken_by_harvester_as_fee` is never called.

**Step 3 — `RespondSignatures.farmer_reward_address_override` is accepted unconditionally.**

In `FarmerAPI._process_respond_signatures` (`chia/farmer/farmer_api.py` lines 916–919):

```python
farmer_reward_address = self.farmer.farmer_target
if response.farmer_reward_address_override is not None:
    farmer_reward_address = response.farmer_reward_address_override
    include_source_signature_data = True
```

There is no check that:
- The override in `RespondSignatures` matches the one declared in `NewProofOfSpace`.
- The fee-quality check passed (or was even run).
- The harvester is entitled to take the fee for this proof.

The resulting `farmer_reward_address` is placed directly into `DeclareProofOfSpace` and broadcast to the full node, which uses it as the farmer reward puzzle hash in the block.

### Impact Explanation

A malicious harvester connected to a farmer can redirect the farmer block reward (currently 0.25 XCH per block) to an attacker-controlled address on **every block** the harvester finds, with no on-chain or protocol-level enforcement preventing it. The farmer's own configured `farmer_target` address is silently replaced. This constitutes unauthorized payout redirection of XCH, matching the **High** impact category: "Bypass of … authorization that enables … payout redirection."

### Likelihood Explanation

CHIP-22 explicitly introduces the concept of third-party harvesters that may be operated by entities other than the farmer. Any such harvester that connects to the farmer's port can exploit this. The farmer has no mechanism to detect or prevent the override beyond reading log warnings. The attack requires only a network connection to the farmer's harvester port and the ability to respond to `RequestSignatures` — no keys, no admin access, no cryptographic break.

### Recommendation

1. **Enforce the fee-quality check**: `notify_farmer_reward_taken_by_harvester_as_fee` should return a boolean indicating whether the override is legitimate. If it returns `False`, the farmer should reject the override and use `self.farmer.farmer_target`.

2. **Cross-validate the two override fields**: In `_process_respond_signatures`, verify that `response.farmer_reward_address_override` matches the address declared in the corresponding `NewProofOfSpace` (which should be stored alongside the proof in `self.farmer.proofs_of_space`). If they differ, reject the override.

3. **Store the fee-quality result**: When `new_proof_of_space` processes a proof, store whether the fee-quality check passed (keyed by `(sp_hash, plot_identifier)`). In `_process_respond_signatures`, only apply the override if the stored result is `True`.

### Proof of Concept

```
Attacker controls a harvester connected to victim farmer.

1. Harvester receives NewSignagePointHarvester from farmer.
2. Harvester finds a valid proof of space.
3. Harvester sends NewProofOfSpace to farmer with:
     farmer_reward_address_override = None   # fee-quality check NOT triggered
     fee_info = None
4. Farmer validates proof, sends RequestSignatures to harvester.
5. Harvester responds with RespondSignatures where:
     farmer_reward_address_override = <attacker_puzzle_hash>
6. Farmer's _process_respond_signatures (farmer_api.py:917-918) accepts
   the override unconditionally:
     farmer_reward_address = response.farmer_reward_address_override
7. Farmer broadcasts DeclareProofOfSpace with attacker_puzzle_hash as
   farmer_reward_puzzle_hash.
8. Full node farms the block; farmer reward coin (0.25 XCH) is created
   at attacker_puzzle_hash instead of farmer's configured address.
```

**Relevant code locations:**

- `chia/protocols/harvester_protocol.py` lines 62–76 (`ProofOfSpaceFeeInfo`, `NewProofOfSpace`) [1](#0-0) 
- `chia/protocols/harvester_protocol.py` lines 131–139 (`RespondSignatures`) [2](#0-1) 
- `chia/farmer/farmer_api.py` lines 128–129 (fee-quality check triggered only on `NewProofOfSpace`) [3](#0-2) 
- `chia/farmer/farmer.py` lines 888–934 (`notify_farmer_reward_taken_by_harvester_as_fee` — log-only, no enforcement) [4](#0-3) 
- `chia/farmer/farmer_api.py` lines 916–919 (unconditional override applied from `RespondSignatures`) [5](#0-4)

### Citations

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
