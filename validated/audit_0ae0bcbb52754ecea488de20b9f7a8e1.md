### Title
Harvester Can Unconditionally Redirect Farmer Block Rewards to Arbitrary Address — (`chia/farmer/farmer_api.py`)

---

### Summary

A connected harvester peer can set `farmer_reward_address_override` in its `RespondSignatures` message to any arbitrary puzzle hash. The farmer unconditionally accepts this override and uses it as the `farmer_reward_address` in `DeclareProofOfSpace`, causing the farmer's 1/8 block reward (XCH) to be sent to the attacker's address. The only "enforcement" is an advisory log warning that does not block the override.

---

### Finding Description

The Chia farmer-harvester protocol (CHIP-22) introduced `farmer_reward_address_override` as a fee mechanism allowing third-party harvesters to take a portion of the farmer reward. The field appears in both `NewProofOfSpace` and `RespondSignatures` messages.

In `chia/protocols/harvester_protocol.py`, `NewProofOfSpace` carries:

```python
farmer_reward_address_override: bytes32 | None
fee_info: ProofOfSpaceFeeInfo | None
``` [1](#0-0) 

When the farmer receives `RespondSignatures` from the harvester, `_process_respond_signatures` in `chia/farmer/farmer_api.py` unconditionally replaces the farmer's configured reward address with the harvester-supplied value:

```python
farmer_reward_address = self.farmer.farmer_target
if response.farmer_reward_address_override is not None:
    farmer_reward_address = response.farmer_reward_address_override
    include_source_signature_data = True
``` [2](#0-1) 

This `farmer_reward_address` is then passed directly into `DeclareProofOfSpace` and broadcast to the full node, which encodes it into the block's foliage as the recipient of the 1/8 farmer coinbase reward. [3](#0-2) 

The only "guard" is `notify_farmer_reward_taken_by_harvester_as_fee`, called earlier in `new_proof_of_space` when `farmer_reward_address_override` is set on the `NewProofOfSpace` message. This function checks a fee quality threshold and emits log warnings if the threshold is violated or `fee_info` is `None` — but it **does not reject the override or abort the block submission flow**:

```python
if new_proof_of_space.farmer_reward_address_override is not None:
    self.farmer.notify_farmer_reward_taken_by_harvester_as_fee(sp, new_proof_of_space)
``` [4](#0-3) 

```python
else:
    self.log.warning(
        "Harvester illegitimately took reward by failing to provide its fee rate ..."
    )
``` [5](#0-4) 

Critically, even if `fee_info` is `None` (no fee declared at all), the override is still applied. There is no cryptographic binding between the `farmer_reward_address_override` in `NewProofOfSpace` and the one in `RespondSignatures` — a harvester can set them independently to any address.

---

### Impact Explanation

**Critical — Unauthorized XCH reward diversion.** Every block won while a malicious harvester is connected results in the farmer's 1/8 coinbase reward (currently 0.25 XCH per block) being permanently sent to the attacker's address on-chain. The farmer has no way to detect or prevent this without monitoring the blockchain externally, as the farmer's own software accepts and signs the diverted block.

---

### Likelihood Explanation

Any harvester that connects to a farmer — including third-party harvesters explicitly supported by CHIP-22 — can perform this attack. No key compromise, admin access, or cryptographic break is required. The attacker only needs to be a connected harvester peer and find a valid proof of space (which is the harvester's normal function). The attack is silent from the farmer's perspective except for a log warning that operators are unlikely to monitor continuously.

---

### Recommendation

The farmer must enforce the fee convention cryptographically, not just via logging. Specifically:

1. **Reject** any `RespondSignatures` where `farmer_reward_address_override` is set but the corresponding `NewProofOfSpace` did not include a valid `fee_info` with a passing fee quality threshold. The check in `notify_farmer_reward_taken_by_harvester_as_fee` should return a boolean and the caller in `new_proof_of_space` should abort the signing flow on failure.
2. **Bind** the override: if `farmer_reward_address_override` is set in `NewProofOfSpace`, the farmer should record the expected override and reject `RespondSignatures` that set a different or unexpected override address.
3. Consider requiring the harvester to sign the `farmer_reward_address_override` with the plot key to prove it is intentional and tied to the specific proof.

---

### Proof of Concept

1. Eve operates a third-party harvester and connects it to Alice's farmer.
2. When Eve's harvester finds a valid proof of space, it sends `NewProofOfSpace` with `farmer_reward_address_override = Eve's puzzle hash` and `fee_info = None`.
3. Alice's farmer calls `notify_farmer_reward_taken_by_harvester_as_fee`, which logs a warning but does not abort.
4. Alice's farmer requests signatures from Eve's harvester via `RequestSignatures`.
5. Eve's harvester responds with `RespondSignatures` containing `farmer_reward_address_override = Eve's puzzle hash`.
6. `_process_respond_signatures` sets `farmer_reward_address = Eve's puzzle hash` and constructs `DeclareProofOfSpace` with Eve's address as the farmer reward recipient.
7. The full node accepts the block; the 1/8 farmer coinbase reward is permanently sent to Eve's address on-chain.
8. Alice receives only the pool reward (7/8) if she is self-pooling, and loses her 1/8 farmer reward with no on-chain recourse. [6](#0-5) [7](#0-6)

### Citations

**File:** chia/protocols/harvester_protocol.py (L68-76)
```python
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

**File:** chia/farmer/farmer_api.py (L128-129)
```python
                if new_proof_of_space.farmer_reward_address_override is not None:
                    self.farmer.notify_farmer_reward_taken_by_harvester_as_fee(sp, new_proof_of_space)
```

**File:** chia/farmer/farmer_api.py (L916-933)
```python
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
