### Title
Harvester Can Unconditionally Redirect Farmer Block Rewards via Unenforced `farmer_reward_address_override` — (`chia/farmer/farmer_api.py`)

### Summary
A malicious harvester can redirect the farmer's XCH block reward to an arbitrary address by setting `farmer_reward_address_override` in `RespondSignatures`. The fee-quality check that is supposed to gate this override only logs a warning and never blocks the override. In `_process_respond_signatures`, the override is accepted unconditionally with no fee-quality enforcement at all.

### Finding Description

The CHIP-22 fee convention allows a third-party harvester to claim the farmer reward for a block by setting `farmer_reward_address_override` in `NewProofOfSpace` and `RespondSignatures`. The farmer is supposed to validate this using a fee-quality threshold before honoring the override.

**Step 1 — `new_proof_of_space` path: check is present but non-enforcing**

When the farmer receives `NewProofOfSpace` with `farmer_reward_address_override != None`, it calls `notify_farmer_reward_taken_by_harvester_as_fee`: [1](#0-0) 

That function computes `fee_quality` and compares it to `fee_info.applied_fee_threshold`, but on failure it only emits a `log.warning` — it never raises, returns a rejection, or sets any flag to block the override: [2](#0-1) 

Execution continues regardless of whether the fee threshold was met.

**Step 2 — `_process_respond_signatures`: no check at all**

After the farmer sends `RequestSignatures` to the harvester, the harvester replies with `RespondSignatures`, which also carries `farmer_reward_address_override`. In `_process_respond_signatures`, this field is accepted unconditionally: [3](#0-2) 

There is no fee-quality check here. The override from `RespondSignatures` is used directly as `farmer_reward_address` in the `DeclareProofOfSpace` message sent to the full node: [4](#0-3) 

`_process_respond_signatures` is called from both the unsolicited `respond_signatures` handler and from `request_signed_values`: [5](#0-4) 

**The `RespondSignatures` protocol message itself carries `farmer_reward_address_override`:** [6](#0-5) 

### Impact Explanation

A malicious harvester (any node that has established a harvester connection to the farmer) can:

1. Send `NewProofOfSpace` with `farmer_reward_address_override` set to an attacker-controlled address. The farmer logs a warning but proceeds.
2. Respond to `RequestSignatures` with `RespondSignatures` also containing `farmer_reward_address_override`.
3. The farmer's `_process_respond_signatures` unconditionally substitutes the attacker's address for `self.farmer.farmer_target`.
4. `DeclareProofOfSpace` is broadcast to the full node with the attacker's puzzle hash as `farmer_reward_address`.
5. The XCH farmer block reward is paid to the attacker's address instead of the farmer's configured address.

This is **unauthorized reward diversion of XCH** — a Critical impact under the allowed scope.

### Likelihood Explanation

Any harvester connected to the farmer can exploit this. Third-party harvesters (e.g., compressed-plot services like DrPlotter, which CHIP-22 explicitly targets) are a common deployment pattern. The attacker needs only a valid harvester connection — no key compromise, no admin access, no cryptographic break required.

### Recommendation

1. `notify_farmer_reward_taken_by_harvester_as_fee` must return a boolean (or raise) and the caller in `new_proof_of_space` must abort processing when the fee quality check fails.
2. `_process_respond_signatures` must independently verify the fee quality against the stored `NewProofOfSpace.fee_info` before honoring `response.farmer_reward_address_override`. The farmer should cache the `fee_info` from `NewProofOfSpace` alongside the proof-of-space record and re-validate it at signature-response time.
3. If `fee_info` is absent and `farmer_reward_address_override` is set, the override must be rejected.

### Proof of Concept

```
Attacker controls a harvester connected to a legitimate farmer.

1. Farmer broadcasts NewSignagePointHarvester.
2. Attacker's harvester finds a valid proof and sends NewProofOfSpace with:
     farmer_reward_address_override = <attacker_puzzle_hash>
     fee_info = ProofOfSpaceFeeInfo(applied_fee_threshold=0xFFFFFFFF)  # always passes
   OR omits fee_info entirely (farmer logs warning, does not block).
3. Farmer calls notify_farmer_reward_taken_by_harvester_as_fee → logs warning → continues.
4. Farmer sends RequestSignatures to harvester.
5. Attacker's harvester responds with RespondSignatures:
     farmer_reward_address_override = <attacker_puzzle_hash>
6. _process_respond_signatures sets farmer_reward_address = attacker_puzzle_hash.
7. DeclareProofOfSpace is sent to full node with attacker's address.
8. Block is won; farmer reward coin is created at attacker_puzzle_hash.
9. Farmer receives no block reward.
```

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
