### Title
Unenforced CHIP-22 Fee Quality Check Allows Any Connected Harvester to Unconditionally Redirect Farmer Block Rewards - (`chia/farmer/farmer_api.py`)

---

### Summary

The CHIP-22 fee convention (`farmer_reward_address_override`) is designed to let third-party harvesters take a proportional fee from the farmer's block reward. However, the fee quality threshold check in `notify_farmer_reward_taken_by_harvester_as_fee()` is **purely advisory** — it only emits log warnings and never rejects or suppresses the override. As a result, any connected harvester (unprivileged) can unconditionally redirect 100% of the farmer's XCH block reward to an arbitrary attacker-controlled address, regardless of whether the fee quality convention is satisfied.

---

### Finding Description

**Step 1 — The override is accepted without enforcement in `new_proof_of_space()`:**

When a harvester sends `NewProofOfSpace` with `farmer_reward_address_override` set, the farmer calls `notify_farmer_reward_taken_by_harvester_as_fee()`: [1](#0-0) 

Inside `notify_farmer_reward_taken_by_harvester_as_fee()`, the fee quality is computed and compared against the harvester-supplied `applied_fee_threshold`. If the threshold is violated — or if `fee_info` is `None` entirely — the function **only logs a warning** and returns normally: [2](#0-1) 

There is no `return`, no exception, and no flag set to suppress the override. The farmer proceeds unconditionally to request signatures from the harvester.

**Step 2 — The override is applied without any guard in `_process_respond_signatures()`:**

When the harvester's `RespondSignatures` arrives with `farmer_reward_address_override` set, the farmer replaces its own configured `farmer_target` with the harvester-supplied address: [3](#0-2) 

This overridden address is then embedded in `DeclareProofOfSpace` as the `farmer_reward_address`: [4](#0-3) 

**Step 3 — The protocol message structures allow arbitrary override values:**

Both `NewProofOfSpace` and `RespondSignatures` carry `farmer_reward_address_override: bytes32 | None` as plain streamable fields with no protocol-level constraint: [5](#0-4) [6](#0-5) 

A malicious harvester can set these to any `bytes32` puzzle hash.

---

### Impact Explanation

The farmer's block reward (1/8 XCH per block, plus transaction fees) is encoded in `FoliageBlockData.farmer_reward_puzzle_hash` and committed to the chain. By redirecting `farmer_reward_address` to an attacker-controlled puzzle hash, the harvester causes the farmer's XCH reward coin to be created at the attacker's address. This is an **unauthorized reward diversion** of XCH — a Critical/High impact per the allowed scope.

The farmer has no recourse after the fact: the reward coin is created on-chain at the attacker's address and is immediately spendable by the attacker.

---

### Likelihood Explanation

Any harvester that is connected to the farmer — including third-party harvesters explicitly supported by CHIP-22 — can exploit this. The farmer actively connects to harvesters and processes their `NewProofOfSpace` messages. No key compromise, admin access, or cryptographic break is required. The attacker only needs to be a connected harvester peer, which is a normal operational relationship.

The attack is silent: the farmer logs a warning but continues farming normally, so the farmer may not notice the diversion until they observe missing rewards.

---

### Recommendation

The fee quality check must be **enforced**, not merely logged. When `farmer_reward_address_override` is present and the fee quality check fails (either `fee_info is None` or `fee_quality > fee_threshold`), the farmer should:

1. **Reject the block-making flow** for that proof of space — do not send `RequestSignatures` to the harvester for that SP.
2. Alternatively, **ignore the override** and use `self.farmer.farmer_target` regardless, treating the violation as a protocol error.

In `notify_farmer_reward_taken_by_harvester_as_fee()`, the function should return a boolean indicating whether the override is legitimate, and `new_proof_of_space()` should gate the signature request on that result. [1](#0-0) [7](#0-6) 

---

### Proof of Concept

1. Attacker operates a harvester and connects it to a victim farmer.
2. Harvester finds a valid proof of space for a signage point.
3. Harvester sends `NewProofOfSpace` to the farmer with:
   - `farmer_reward_address_override = attacker_puzzle_hash`
   - `fee_info = None` (no fee info provided — convention violated)
4. Farmer calls `notify_farmer_reward_taken_by_harvester_as_fee()`, which logs: *"Harvester illegitimately took reward by failing to provide its fee rate"* — but does **not** abort.
5. Farmer sends `RequestSignatures` to the harvester.
6. Harvester responds with `RespondSignatures` containing `farmer_reward_address_override = attacker_puzzle_hash`.
7. Farmer executes lines 917–918 of `farmer_api.py`, replacing `farmer_reward_address` with `attacker_puzzle_hash`.
8. Farmer broadcasts `DeclareProofOfSpace` with `farmer_reward_address = attacker_puzzle_hash`.
9. Full node creates the farmer reward coin at `attacker_puzzle_hash`.
10. Attacker receives the farmer's 1/8 XCH block reward. [3](#0-2) [8](#0-7)

### Citations

**File:** chia/farmer/farmer_api.py (L127-129)
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
