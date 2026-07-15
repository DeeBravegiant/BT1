### Title
Malicious Harvester Can Bypass CHIP-22 Fee Quality Check to Unconditionally Redirect Farmer Block Reward - (File: chia/farmer/farmer_api.py)

### Summary

The CHIP-22 fee convention allows a third-party harvester to redirect the farmer's block reward to itself by setting `farmer_reward_address_override`. A fee quality check is supposed to gate this, but it is only advisory (log-only) and is only triggered from the `NewProofOfSpace` message path. A malicious harvester can bypass it entirely by omitting the override in `NewProofOfSpace` and injecting it only in `RespondSignatures`, causing the farmer to unconditionally redirect its block reward to the attacker's address.

### Finding Description

The CHIP-22 convention (referenced in `chia/farmer/farmer.py`) defines a probabilistic fee quality threshold: a harvester is entitled to take the farmer reward only when `fee_quality <= applied_fee_threshold`, where `fee_quality = hash(proof || challenge)[28:32]`.

**Check location (advisory only):**

In `FarmerAPI.new_proof_of_space`, when `new_proof_of_space.farmer_reward_address_override is not None`, the farmer calls `notify_farmer_reward_taken_by_harvester_as_fee()`: [1](#0-0) 

Inside that function, the check is performed but **only logs a warning on failure — no rejection occurs**: [2](#0-1) 

**Actual redirect location (no check):**

The actual farmer reward address substitution happens in `_process_respond_signatures`, which processes the `RespondSignatures` message from the harvester: [3](#0-2) 

There is **no fee quality check here**. The override is accepted unconditionally if non-`None`.

**Attack path:**

1. Malicious harvester connects to the farmer (unprivileged — any harvester can connect).
2. Harvester sends `NewProofOfSpace` with `farmer_reward_address_override=None` → the fee quality check in `notify_farmer_reward_taken_by_harvester_as_fee` is **never triggered**.
3. Farmer sends `RequestSignatures` to the harvester.
4. Harvester responds with `RespondSignatures` where `farmer_reward_address_override` is set to the attacker's puzzle hash.
5. `_process_respond_signatures` accepts the override unconditionally and builds `DeclareProofOfSpace` with `farmer_reward_address = attacker_address`. [4](#0-3) 

6. The full node receives `DeclareProofOfSpace` and uses `request.farmer_puzzle_hash` (the attacker's address) to build the unfinished block: [5](#0-4) 

7. Block is farmed; the farmer's base reward (1/8 of block reward) is paid to the attacker's address. No consensus-level check validates that `farmer_puzzle_hash` matches the farmer's configured address for non-genesis blocks: [6](#0-5) 

### Impact Explanation

A malicious third-party harvester can steal the farmer's entire block reward (base farmer reward = 1/8 of block reward, e.g. 0.25 XCH per block at current halving) on every block the farmer wins, by injecting an arbitrary `farmer_reward_address_override` in `RespondSignatures`. This is an unauthorized payout redirection of XCH with no on-chain or protocol-level enforcement to stop it.

### Likelihood Explanation

Any harvester that connects to the farmer can execute this attack. Third-party harvesters are explicitly supported by CHIP-22 and are a common deployment pattern. The attacker needs no special privileges, no keys, and no cryptographic capability beyond what a normal harvester already has. The attack is silent — the farmer sees no error, only a missing reward.

### Recommendation

The fee quality check must be enforced at the point where the redirect is applied, not only at the `NewProofOfSpace` receipt point. In `_process_respond_signatures`, before accepting `response.farmer_reward_address_override`, the farmer should:

1. Verify that the corresponding `NewProofOfSpace` for this `sp_hash`/`plot_identifier` also carried a non-`None` `farmer_reward_address_override` (i.e., the harvester declared intent upfront).
2. Re-verify the fee quality check: `calculate_harvester_fee_quality(proof, challenge) <= applied_fee_threshold`.
3. **Reject** (return `None`) rather than log a warning when the check fails.

The fee quality check function already exists: [7](#0-6) 

### Proof of Concept

```python
# Malicious harvester implementation (pseudocode)

class MaliciousHarvesterAPI:
    ATTACKER_ADDRESS = bytes32(b"attacker_puzzle_hash_32_bytes___")

    async def new_signage_point_harvester(self, new_challenge, peer):
        # Find a valid proof of space for this challenge
        proof = find_proof(new_challenge)
        if proof:
            # Step 1: Send NewProofOfSpace WITHOUT farmer_reward_address_override
            # This bypasses the fee quality check in notify_farmer_reward_taken_by_harvester_as_fee
            msg = NewProofOfSpace(
                challenge_hash=new_challenge.challenge_hash,
                sp_hash=new_challenge.sp_hash,
                plot_identifier="attacker.plot",
                proof=proof,
                signage_point_index=new_challenge.signage_point_index,
                include_source_signature_data=False,
                farmer_reward_address_override=None,  # <-- No override here; check not triggered
                fee_info=None,
            )
            await peer.send_message(make_msg(ProtocolMessageTypes.new_proof_of_space, msg))

    async def request_signatures(self, request, peer):
        # Step 2: Sign the messages as normal, but inject farmer_reward_address_override
        # in RespondSignatures — no fee quality check is performed here by the farmer
        signatures = sign_messages(request.messages)
        response = RespondSignatures(
            plot_identifier=request.plot_identifier,
            challenge_hash=request.challenge_hash,
            sp_hash=request.sp_hash,
            local_pk=self.local_pk,
            farmer_pk=self.farmer_pk,
            message_signatures=signatures,
            include_source_signature_data=False,
            farmer_reward_address_override=self.ATTACKER_ADDRESS,  # <-- Injected here
        )
        # Farmer's _process_respond_signatures accepts this unconditionally
        # and builds DeclareProofOfSpace with farmer_reward_address = ATTACKER_ADDRESS
        await peer.send_message(make_msg(ProtocolMessageTypes.respond_signatures, response))
```

The farmer's `_process_respond_signatures` at lines 916–919 will accept the override without any fee quality validation, and the resulting block will pay the farmer reward to `ATTACKER_ADDRESS`. [8](#0-7)

### Citations

**File:** chia/farmer/farmer_api.py (L128-129)
```python
                if new_proof_of_space.farmer_reward_address_override is not None:
                    self.farmer.notify_farmer_reward_taken_by_harvester_as_fee(sp, new_proof_of_space)
```

**File:** chia/farmer/farmer_api.py (L602-614)
```python
    @metadata.request()
    async def respond_signatures(self, response: harvester_protocol.RespondSignatures) -> None:
        request = self._process_respond_signatures(response)
        if request is None:
            return None

        message: Message | None = None
        if isinstance(request, DeclareProofOfSpace):
            self.farmer.state_changed("proof", {"proof": request, "passed_filter": True})
            message = make_msg(ProtocolMessageTypes.declare_proof_of_space, request)
        if isinstance(request, SignedValues):
            message = make_msg(ProtocolMessageTypes.signed_values, request)
        await self.farmer.server.send_to_all([message], NodeType.FULL_NODE)
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

**File:** chia/farmer/farmer.py (L911-934)
```python
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

**File:** chia/farmer/farmer.py (L937-942)
```python
def calculate_harvester_fee_quality(proof: bytes, challenge: bytes32) -> uint32:
    """
    This calculates the 'fee quality' given a convention between farmers and third party harvesters.
    See CHIP-22: https://github.com/Chia-Network/chips/pull/88
    """
    return uint32(int.from_bytes(std_hash(proof + challenge)[32 - 4 :], byteorder="big", signed=False))
```

**File:** chia/full_node/full_node_api.py (L1069-1069)
```python
                farmer_ph = request.farmer_puzzle_hash
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
