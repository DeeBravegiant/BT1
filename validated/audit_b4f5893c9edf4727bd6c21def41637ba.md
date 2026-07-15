### Title
Unvalidated `farmer_reward_address_override` Allows Malicious Harvester to Redirect Farmer Block Reward to Unspendable Address - (File: `chia/farmer/farmer_api.py`)

### Summary
In `FarmerAPI._process_respond_signatures`, the `farmer_reward_address_override` field supplied by a connected harvester via `RespondSignatures` is accepted and used as the farmer reward puzzle hash without any validation. A malicious third-party harvester (CHIP-22 model) can set this field to `bytes32.zeros` (all-zero puzzle hash), causing the farmer's block reward to be permanently sent to an unspendable address. There is no guard checking that the override is a non-zero, valid puzzle hash before it is embedded in `DeclareProofOfSpace` and ultimately in the block's foliage.

### Finding Description

In `_process_respond_signatures` (`chia/farmer/farmer_api.py`), the farmer reward address is determined as follows:

```python
farmer_reward_address = self.farmer.farmer_target
if response.farmer_reward_address_override is not None:
    farmer_reward_address = response.farmer_reward_address_override
    include_source_signature_data = True
``` [1](#0-0) 

If `response.farmer_reward_address_override` is any non-`None` `bytes32` value — including `bytes32.zeros` — it is used directly as `farmer_reward_address` with no further validation. This value is then passed into `DeclareProofOfSpace.farmer_puzzle_hash`: [2](#0-1) 

The full node's `declare_proof_of_space` handler reads this field directly:

```python
farmer_ph = request.farmer_puzzle_hash
``` [3](#0-2) 

and passes it to `create_unfinished_block` as `farmer_reward_puzzle_hash`, which embeds it in the block's `FoliageBlockData`. Consensus validation in `validate_unfinished_header_block` does **not** check the farmer reward puzzle hash for non-genesis blocks beyond the genesis pre-farm check: [4](#0-3) 

The only response to an invalid override is a log warning inside `notify_farmer_reward_taken_by_harvester_as_fee`, which does not block block creation: [5](#0-4) 

The `RespondSignatures` protocol message explicitly allows `farmer_reward_address_override: bytes32 | None`: [6](#0-5) 

### Impact Explanation

A malicious third-party harvester (as introduced by CHIP-22) that is connected to a farmer can set `farmer_reward_address_override = bytes32.zeros` in its `RespondSignatures` message. The farmer will embed this as the `farmer_puzzle_hash` in the block, causing the farmer's coinbase reward (1/8 of the block reward, currently 0.25 XCH) to be created at the all-zero puzzle hash — an address for which no spending key exists. The reward coin is permanently unspendable. This constitutes unauthorized payout redirection and permanent loss of XCH for the farmer.

### Likelihood Explanation

The CHIP-22 third-party harvester model is a supported production feature. A farmer may connect to external harvesters they do not fully control. Any such harvester can trivially set `farmer_reward_address_override` to `bytes32.zeros` on any winning proof of space. The farmer has no mechanism to reject the override short of not using the proof at all.

### Recommendation

Before using `farmer_reward_address_override` as the farmer reward address, validate that it is not `bytes32.zeros` (and optionally that it matches an expected allowlist or passes a minimum sanity check). If the override is zero or otherwise invalid, fall back to `self.farmer.farmer_target` and log an error. The check should be added in `_process_respond_signatures` immediately before the override is applied:

```python
if response.farmer_reward_address_override is not None:
    if response.farmer_reward_address_override == bytes32.zeros:
        self.farmer.log.error("Harvester supplied zero farmer_reward_address_override; ignoring.")
    else:
        farmer_reward_address = response.farmer_reward_address_override
        include_source_signature_data = True
```

### Proof of Concept

1. A malicious harvester connects to a farmer node.
2. The harvester finds a valid proof of space for a signage point.
3. The harvester sends `NewProofOfSpace` with `farmer_reward_address_override = bytes32.zeros`.
4. The farmer calls `notify_farmer_reward_taken_by_harvester_as_fee`, which logs a warning but does not abort.
5. The farmer sends `RequestSignatures` to the harvester.
6. The harvester responds with `RespondSignatures` containing `farmer_reward_address_override = bytes32.zeros`.
7. `_process_respond_signatures` sets `farmer_reward_address = bytes32.zeros` and returns a `DeclareProofOfSpace` with `farmer_puzzle_hash = bytes32.zeros`.
8. The full node creates an `UnfinishedBlock` with `farmer_reward_puzzle_hash = bytes32.zeros` in the foliage.
9. Consensus validation passes (no check on farmer puzzle hash for non-genesis blocks).
10. The block is finalized; the farmer coinbase reward coin is created at `bytes32.zeros` and is permanently unspendable. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

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

**File:** chia/full_node/full_node_api.py (L1062-1074)
```python
            if prev_b is None:
                pool_target = PoolTarget(
                    self.full_node.constants.GENESIS_PRE_FARM_POOL_PUZZLE_HASH,
                    uint32(0),
                )
                farmer_ph = self.full_node.constants.GENESIS_PRE_FARM_FARMER_PUZZLE_HASH
            else:
                farmer_ph = request.farmer_puzzle_hash
                if request.proof_of_space.pool_contract_puzzle_hash is not None:
                    pool_target = PoolTarget(request.proof_of_space.pool_contract_puzzle_hash, uint32(0))
                else:
                    assert request.pool_target is not None
                    pool_target = request.pool_target
```

**File:** chia/consensus/block_header_validation.py (L763-795)
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
    # 20b. If pospace has a pool pk, check pool target signature. Should not check this for genesis block.
    elif header_block.reward_chain_block.proof_of_space.pool_public_key is not None:
        assert header_block.reward_chain_block.proof_of_space.pool_contract_puzzle_hash is None
        assert header_block.foliage.foliage_block_data.pool_signature is not None

        if not AugSchemeMPL.verify(
            header_block.reward_chain_block.proof_of_space.pool_public_key,
            bytes(header_block.foliage.foliage_block_data.pool_target),
            header_block.foliage.foliage_block_data.pool_signature,
        ):
            return None, ValidationError(Err.INVALID_POOL_SIGNATURE)
    else:
        # 20c. Otherwise, the plot is associated with a contract puzzle hash, not a public key
        assert header_block.reward_chain_block.proof_of_space.pool_contract_puzzle_hash is not None
        if (
            header_block.foliage.foliage_block_data.pool_target.puzzle_hash
            != header_block.reward_chain_block.proof_of_space.pool_contract_puzzle_hash
        ):
            return None, ValidationError(Err.INVALID_POOL_TARGET)

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

**File:** chia/consensus/block_creation.py (L287-307)
```python
def create_unfinished_block(
    constants: ConsensusConstants,
    sub_slot_start_total_iters: uint128,
    infusion_point_total_iters: uint128,
    signage_point_index: uint8,
    sp_iters: uint64,
    proof_of_space: ProofOfSpace,
    slot_cc_challenge: bytes32,
    farmer_reward_puzzle_hash: bytes32,
    pool_target: PoolTarget,
    get_plot_signature: Callable[[bytes32, G1Element], G2Element],
    get_pool_signature: Callable[[PoolTarget, G1Element | None], G2Element | None],
    signage_point: SignagePoint,
    timestamp: uint64,
    blocks: BlockRecordsProtocol,
    seed: bytes = b"",
    new_block_gen: NewBlockGenerator | None = None,
    prev_block: BlockRecord | None = None,
    finished_sub_slots_input: list[EndOfSubSlotBundle] | None = None,
    compute_fees: Callable[[Sequence[Coin], Sequence[Coin]], uint64] = compute_block_fee,
) -> UnfinishedBlock:
```
