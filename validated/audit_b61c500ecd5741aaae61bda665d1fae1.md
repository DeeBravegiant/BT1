### Title
Unvalidated `farmer_reward_address_override` in `RespondSignatures` Allows Silent Farmer Block Reward Theft - (`chia/farmer/farmer_api.py`)

### Summary
The Chia farmer accepts a `farmer_reward_address_override` field from a harvester's `RespondSignatures` message and uses it unconditionally as the block's farmer reward puzzle hash. The farmer logs a fee-redirection warning only when `NewProofOfSpace.farmer_reward_address_override` is non-None, but the actual reward address used in block creation comes from `RespondSignatures.farmer_reward_address_override`, which is never cross-validated against the original `NewProofOfSpace` value. A malicious third-party harvester (a supported use case under CHIP-22) can silently redirect the farmer's entire block reward (0.25 XCH) to any arbitrary address by sending `NewProofOfSpace` with `farmer_reward_address_override=None` and then `RespondSignatures` with `farmer_reward_address_override=<attacker_address>`.

### Finding Description

**Root cause — two separate fields, checked in different places:**

`NewProofOfSpace` carries `farmer_reward_address_override` and is the basis for the fee-warning log: [1](#0-0) [2](#0-1) 

`RespondSignatures` also carries `farmer_reward_address_override` and is what the farmer actually uses when building `DeclareProofOfSpace`: [3](#0-2) [4](#0-3) 

The farmer stores only `plot_identifier` and `proof` from `NewProofOfSpace` — the `farmer_reward_address_override` value is discarded and never compared against the later `RespondSignatures`: [5](#0-4) 

**Exploit path:**

1. A malicious third-party harvester (connected via mTLS, explicitly supported by CHIP-22) sends `NewProofOfSpace` with `farmer_reward_address_override=None`. The farmer logs no fee warning.
2. The farmer sends `RequestSignatures` to the harvester (this message contains no `farmer_reward_address_override`).
3. The harvester responds with `RespondSignatures` where `farmer_reward_address_override=<attacker_controlled_address>`.
4. `_process_respond_signatures` unconditionally substitutes the attacker's address for `farmer_reward_address` with no cross-check: [6](#0-5) 

5. `DeclareProofOfSpace` is sent to the full node with the attacker's puzzle hash as `farmer_reward_puzzle_hash`. The full node's consensus layer does not validate this field against the farmer's configured address — it only checks the genesis pre-farm hash for block 0: [7](#0-6) 

6. The block is accepted and the 0.25 XCH farmer reward coin is created at the attacker's address.

**The standard harvester always sets this field to `None`**, so the vulnerability is only reachable via a malicious third-party harvester: [8](#0-7) 

### Impact Explanation

A malicious third-party harvester can silently redirect the farmer's block reward (0.25 XCH per won block) to any arbitrary address. The farmer receives no warning because the warning path checks `NewProofOfSpace.farmer_reward_address_override`, not `RespondSignatures.farmer_reward_address_override`. This is direct, on-chain theft of XCH from the farmer with no recourse. Impact category: **High — unauthorized reward diversion affecting XCH**.

### Likelihood Explanation

CHIP-22 explicitly introduced third-party harvesters as a supported use case, meaning farmers are expected to connect to harvesters they do not fully control. Any operator of a third-party harvester service can exploit this silently on every block the farmer wins. The attacker requires only a valid mTLS connection to the farmer's harvester port, which is the normal operating condition for third-party harvesters.

### Recommendation

When `_process_respond_signatures` processes a `RespondSignatures` message, validate that `response.farmer_reward_address_override` matches the value stored from the corresponding `NewProofOfSpace`. Concretely:

1. When storing a proof in `new_proof_of_space`, also store `new_proof_of_space.farmer_reward_address_override` alongside `plot_identifier` and `proof` in `self.farmer.proofs_of_space`.
2. In `_process_respond_signatures`, after retrieving `pospace`, retrieve the stored override and assert `response.farmer_reward_address_override == stored_override`. Reject the response if they differ.

This mirrors the pattern used in the external report's fix: check the helper/override field against a known-good value before using it.

### Proof of Concept

1. Operator runs a third-party harvester that intercepts the `new_signage_point_harvester` flow.
2. When a valid proof is found, the harvester sends `NewProofOfSpace` with `farmer_reward_address_override=None` (no warning triggered on farmer side).
3. When the farmer sends `RequestSignatures`, the harvester responds with `RespondSignatures` where `farmer_reward_address_override = attacker_puzzle_hash`.
4. The farmer's `_process_respond_signatures` (lines 916–919) substitutes `attacker_puzzle_hash` for `farmer_reward_address` with no validation.
5. `DeclareProofOfSpace` is forwarded to the full node; the resulting block's foliage contains `farmer_reward_puzzle_hash = attacker_puzzle_hash`.
6. On block confirmation, the 0.25 XCH farmer reward coin is created at `attacker_puzzle_hash`. The farmer's wallet receives nothing.

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

**File:** chia/farmer/farmer_api.py (L170-177)
```python
                if new_proof_of_space.sp_hash not in self.farmer.proofs_of_space:
                    self.farmer.proofs_of_space[new_proof_of_space.sp_hash] = []
                self.farmer.proofs_of_space[new_proof_of_space.sp_hash].append(
                    (
                        new_proof_of_space.plot_identifier,
                        new_proof_of_space.proof,
                    )
                )
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

**File:** chia/harvester/harvester_api.py (L515-526)
```python
        response: harvester_protocol.RespondSignatures = harvester_protocol.RespondSignatures(
            request.plot_identifier,
            request.challenge_hash,
            request.sp_hash,
            local_sk.get_g1(),
            farmer_public_key,
            message_signatures,
            False,
            None,
        )

        return make_msg(ProtocolMessageTypes.respond_signatures, response)
```
