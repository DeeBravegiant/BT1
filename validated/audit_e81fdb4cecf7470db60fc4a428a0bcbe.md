### Title
Malicious Third-Party Harvester Can Unconditionally Divert Farmer Block Reward to Arbitrary Address - (`chia/farmer/farmer_api.py`)

### Summary

The CHIP-22 fee convention allows third-party harvesters to redirect the farmer's XCH block reward by setting `farmer_reward_address_override`. The farmer's fee-quality enforcement is **logging-only**: a malicious harvester can always divert 100% of the farmer reward to an attacker-controlled address with no on-chain or protocol-level rejection.

### Finding Description

When a harvester wins a block, it can set `farmer_reward_address_override` in either `NewProofOfSpace` or `RespondSignatures`. The farmer is supposed to validate this against a "fee quality" convention (CHIP-22): the harvester's proof quality must meet a threshold before it is entitled to redirect the reward.

In `farmer_api.py`, when `farmer_reward_address_override` is present in `NewProofOfSpace`, the farmer calls `notify_farmer_reward_taken_by_harvester_as_fee`:

```python
if new_proof_of_space.farmer_reward_address_override is not None:
    self.farmer.notify_farmer_reward_taken_by_harvester_as_fee(sp, new_proof_of_space)
``` [1](#0-0) 

That function only logs warnings — it never returns a rejection signal and the caller never checks a return value:

```python
def notify_farmer_reward_taken_by_harvester_as_fee(...):
    ...
    if fee_quality <= fee_threshold:
        self.log.info(...)   # passes
    else:
        self.log.warning(...)  # fails — but no rejection
    # else branch: no fee_info provided → also just a warning
``` [2](#0-1) 

Then, in `_process_respond_signatures`, the override is applied **unconditionally**:

```python
farmer_reward_address = self.farmer.farmer_target
if response.farmer_reward_address_override is not None:
    farmer_reward_address = response.farmer_reward_address_override
    include_source_signature_data = True
``` [3](#0-2) 

The resulting `farmer_reward_address` is placed directly into `DeclareProofOfSpace` and propagated to the full node, which embeds it as `farmer_reward_puzzle_hash` in the block's foliage. The full node validates the signature over the foliage data but does **not** validate that the `farmer_reward_puzzle_hash` matches the farmer's configured target. [4](#0-3) 

The `RespondSignatures` protocol message explicitly carries this optional field:

```python
class RespondSignatures(Streamable):
    ...
    farmer_reward_address_override: bytes32 | None
``` [5](#0-4) 

### Impact Explanation

A malicious third-party harvester can set `farmer_reward_address_override` to any attacker-controlled puzzle hash. The farmer accepts this override unconditionally regardless of whether the fee quality check passes or fails. Every block won while connected to the malicious harvester will pay the farmer's XCH block reward (currently ~0.25 XCH) to the attacker instead of the farmer. This is unauthorized reward diversion of XCH via a reachable, unprivileged protocol path.

### Likelihood Explanation

Third-party harvesters are a supported and common configuration (pool farming, compressed plot services). Any harvester that connects to a farmer node can exploit this. The attacker needs only to be an accepted harvester peer — no key compromise or admin access is required. The farmer has no way to detect or prevent the diversion at the protocol level.

### Recommendation

The farmer must enforce the fee quality check, not merely log it. If `farmer_reward_address_override` is set and the fee quality does not meet the harvester's declared threshold (or no `fee_info` is provided), the farmer should **reject** the `RespondSignatures` message and not proceed with block creation. Specifically, `_process_respond_signatures` should return `None` (dropping the block) when the fee quality check fails, rather than silently accepting the overridden address.

### Proof of Concept

1. Attacker operates a harvester that connects to a victim farmer.
2. When a proof of space is found, the harvester sends `NewProofOfSpace` with `farmer_reward_address_override = attacker_puzzle_hash` and either no `fee_info` or `fee_info.applied_fee_threshold = 0xFFFFFFFF`.
3. The farmer calls `notify_farmer_reward_taken_by_harvester_as_fee`, which logs a warning but does not reject.
4. The farmer requests signatures from the harvester; the harvester responds with `RespondSignatures` also carrying `farmer_reward_address_override = attacker_puzzle_hash`.
5. In `_process_respond_signatures`, `farmer_reward_address` is set to `attacker_puzzle_hash` unconditionally.
6. `DeclareProofOfSpace` is sent to the full node with `farmer_reward_address = attacker_puzzle_hash`.
7. The full node creates a block with `farmer_reward_puzzle_hash = attacker_puzzle_hash`; the farmer's XCH reward is paid to the attacker. [1](#0-0) [6](#0-5) [7](#0-6)

### Citations

**File:** chia/farmer/farmer_api.py (L128-129)
```python
                if new_proof_of_space.farmer_reward_address_override is not None:
                    self.farmer.notify_farmer_reward_taken_by_harvester_as_fee(sp, new_proof_of_space)
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
