### Title
Malicious Third-Party Harvester Can Redirect Farmer Block Reward to Unspendable Address via Unchecked `farmer_reward_address_override` - (File: chia/farmer/farmer_api.py)

### Summary
CHIP-22 allows third-party harvesters to override the farmer's reward puzzle hash via `farmer_reward_address_override` in `RespondSignatures`. The farmer node accepts any non-`None` `bytes32` value — including `bytes32.zeros` — without validating it is a non-zero/non-burn address. A malicious harvester can set this field to all-zero bytes, causing the farmer's block reward to be permanently sent to an unspendable puzzle hash.

### Finding Description

The CHIP-22 protocol extension introduces `farmer_reward_address_override: bytes32 | None` in both `NewProofOfSpace` and `RespondSignatures` harvester protocol messages. [1](#0-0) [2](#0-1) 

In `FarmerAPI._process_respond_signatures`, the farmer unconditionally replaces its own configured `farmer_target` with whatever `bytes32` the harvester supplies, as long as it is not `None`: [3](#0-2) 

This value is then placed directly into `DeclareProofOfSpace.farmer_puzzle_hash` and forwarded to the full node: [4](#0-3) 

The full node uses it verbatim as `farmer_ph` when constructing the unfinished block: [5](#0-4) 

Block header validation does **not** constrain the farmer reward puzzle hash for non-genesis blocks — only the genesis block is checked against `GENESIS_PRE_FARM_FARMER_PUZZLE_HASH`: [6](#0-5) 

The `notify_farmer_reward_taken_by_harvester_as_fee` call that precedes the override only **logs** a warning when the fee quality threshold is violated; it does not block the override or reject the `RespondSignatures` message: [7](#0-6) [8](#0-7) 

### Impact Explanation

A malicious third-party harvester sets `farmer_reward_address_override = bytes32(b'\x00' * 32)` in its `RespondSignatures` reply. The farmer node accepts this without any zero-check, embeds it as `farmer_puzzle_hash` in `DeclareProofOfSpace`, and the full node builds a valid block paying the farmer coinbase reward (currently 0.25 XCH) to the all-zeros puzzle hash — an address for which no spending key exists. The reward is permanently unspendable (burned). This constitutes unauthorized payout redirection of XCH from the farmer's wallet.

### Likelihood Explanation

Low. The attacker must be a third-party harvester that the farmer has deliberately connected to (e.g., a DrPlotter-style service). However, CHIP-22 explicitly enables this connection model, and the farmer has no in-protocol mechanism to distinguish a legitimate fee override from a burn-address override. Any block won by the malicious harvester triggers the loss.

### Recommendation

In `FarmerAPI._process_respond_signatures`, before accepting `farmer_reward_address_override`, validate it is not the zero puzzle hash:

```python
if response.farmer_reward_address_override is not None:
    if response.farmer_reward_address_override == bytes32.zeros:
        self.farmer.log.error("Harvester supplied zero farmer_reward_address_override; ignoring.")
        return None
    farmer_reward_address = response.farmer_reward_address_override
    include_source_signature_data = True
```

The same guard should be applied when processing `NewProofOfSpace.farmer_reward_address_override` in `new_proof_of_space`.

### Proof of Concept

1. Operator connects their farmer to a malicious third-party harvester service (normal CHIP-22 usage).
2. Harvester finds a valid proof of space and sends `NewProofOfSpace` with `farmer_reward_address_override = bytes32(b'\x00'*32)`.
3. Farmer calls `notify_farmer_reward_taken_by_harvester_as_fee` — this only logs; execution continues.
4. Farmer sends `RequestSignatures` to the harvester.
5. Harvester replies with `RespondSignatures` also carrying `farmer_reward_address_override = bytes32(b'\x00'*32)`.
6. `_process_respond_signatures` sets `farmer_reward_address = bytes32.zeros` (line 918) and returns a `DeclareProofOfSpace` with `farmer_puzzle_hash = bytes32.zeros`.
7. Full node builds and finalises the block; the farmer coinbase coin is created with `puzzle_hash = bytes32.zeros`.
8. No private key can spend this coin; the reward is permanently lost.

### Citations

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

**File:** chia/full_node/full_node_api.py (L1068-1074)
```python
            else:
                farmer_ph = request.farmer_puzzle_hash
                if request.proof_of_space.pool_contract_puzzle_hash is not None:
                    pool_target = PoolTarget(request.proof_of_space.pool_contract_puzzle_hash, uint32(0))
                else:
                    assert request.pool_target is not None
                    pool_target = request.pool_target
```

**File:** chia/consensus/block_header_validation.py (L763-775)
```python
    # 20a. Check pre-farm puzzle hashes for genesis block.
    if genesis_block:
        if (
            header_block.foliage.foliage_block_data.pool_target.puzzle_hash
            != constants.GENESIS_PRE_FARM_POOL_PUZZLE_HASH
        ):
            log.error(f"Pool target {header_block.foliage.foliage_block_data.pool_target} hb {header_block}")
            return None, ValidationError(Err.INVALID_PREFARM)
        if (
            header_block.foliage.foliage_block_data.farmer_reward_puzzle_hash
            != constants.GENESIS_PRE_FARM_FARMER_PUZZLE_HASH
        ):
            return None, ValidationError(Err.INVALID_PREFARM)
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
