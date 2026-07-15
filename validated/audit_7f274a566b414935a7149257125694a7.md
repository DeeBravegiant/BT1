### Title
Malicious Harvester Unconditionally Redirects Farmer Block Rewards via Unvalidated `farmer_reward_address_override` — (File: chia/farmer/farmer_api.py)

---

### Summary

`FarmerAPI._process_respond_signatures()` unconditionally substitutes the farmer's configured reward address with any arbitrary puzzle hash supplied by a harvester in the `RespondSignatures.farmer_reward_address_override` field. The CHIP-22 fee-quality check that is supposed to gate this substitution only emits a log warning on failure and never rejects the override. A malicious third-party harvester can therefore redirect every XCH block reward to an attacker-controlled address.

---

### Finding Description

`RespondSignatures` (defined in `chia/protocols/harvester_protocol.py`) carries an optional `farmer_reward_address_override` field introduced by CHIP-22 to let third-party harvesters claim a fee share. [1](#0-0) 

In `FarmerAPI._process_respond_signatures()`, this field is accepted without any fee-quality validation:

```python
farmer_reward_address = self.farmer.farmer_target
if response.farmer_reward_address_override is not None:
    farmer_reward_address = response.farmer_reward_address_override
    include_source_signature_data = True
``` [2](#0-1) 

The resulting `farmer_reward_address` is then placed directly into `DeclareProofOfSpace` and broadcast to the full node: [3](#0-2) 

The only fee-quality check in the codebase lives in `Farmer.notify_farmer_reward_taken_by_harvester_as_fee()`, called from `new_proof_of_space()` when `NewProofOfSpace.farmer_reward_address_override is not None`: [4](#0-3) 

That function computes `fee_quality` and compares it to `applied_fee_threshold`, but on failure it only logs a warning — it never returns an error, never sets a flag, and never prevents the override from being used later: [5](#0-4) 

There are two independent paths to exploitation:

**Path A (normal flow, fee-quality bypass):** The harvester sends `NewProofOfSpace` with `fee_info.applied_fee_threshold = 0xFFFFFFFF`. Because `fee_quality` is always `<= 0xFFFFFFFF`, the check logs "Fee threshold passed." The farmer then requests signatures; the harvester replies with `RespondSignatures` carrying `farmer_reward_address_override = attacker_address`. `_process_respond_signatures()` accepts it with no further check.

**Path B (unsolicited `RespondSignatures`):** The `respond_signatures` handler is registered without `peer_required=True`: [6](#0-5) 

A connected harvester peer can send an unsolicited `RespondSignatures` for any `sp_hash` / `plot_identifier` pair it has observed, with an arbitrary `farmer_reward_address_override`, completely skipping even the logging-only fee-quality check in `new_proof_of_space()`.

---

### Impact Explanation

Every XCH block reward for the affected farmer is redirected to the attacker's puzzle hash. This is a direct, permanent diversion of XCH from the farmer's configured reward address to an attacker-controlled address — matching the "reward diversion" criterion in the Critical/High impact scope. The `farmer_reward_puzzle_hash` embedded in `FoliageBlockData` is consensus-enforced; once the block is accepted, the reward cannot be recovered.

---

### Likelihood Explanation

Third-party harvesters are an explicitly supported and common deployment pattern (CHIP-22). Any peer that successfully completes the harvester handshake can send `RespondSignatures` messages. No key material, admin access, or cryptographic break is required — only a network connection to the farmer's harvester port.

---

### Recommendation

1. **Enforce the fee-quality threshold, not just log it.** `notify_farmer_reward_taken_by_harvester_as_fee()` should return a `bool` indicating validity. `_process_respond_signatures()` must check this result and fall back to `self.farmer.farmer_target` when the threshold is not met.
2. **Validate `RespondSignatures.farmer_reward_address_override` against the originating `NewProofOfSpace`.** Store the override (and its validated fee-quality result) when processing `new_proof_of_space()`, and in `_process_respond_signatures()` only accept an override that was previously validated for the same `sp_hash` / `plot_identifier`.
3. **Reject unsolicited overrides.** If `farmer_reward_address_override` is set in a `RespondSignatures` that was not preceded by a validated `NewProofOfSpace` carrying the same override, discard it.

---

### Proof of Concept

```
1. Attacker operates a third-party harvester connected to the victim farmer.

2. Harvester finds a valid proof and sends NewProofOfSpace with:
     farmer_reward_address_override = attacker_puzzle_hash
     fee_info.applied_fee_threshold  = 0xFFFFFFFF   # always passes

3. Farmer calls notify_farmer_reward_taken_by_harvester_as_fee():
     fee_quality = hash(proof || challenge)[28:32]  # any uint32
     fee_quality <= 0xFFFFFFFF  → True → logs "Fee threshold passed"
     (no rejection, no flag set)

4. Farmer sends RequestSignatures to the harvester.

5. Harvester replies with RespondSignatures:
     farmer_reward_address_override = attacker_puzzle_hash

6. _process_respond_signatures() executes:
     farmer_reward_address = self.farmer.farmer_target   # overwritten next
     if response.farmer_reward_address_override is not None:
         farmer_reward_address = attacker_puzzle_hash    # no check here

7. DeclareProofOfSpace is broadcast to the full node with
     farmer_reward_puzzle_hash = attacker_puzzle_hash.

8. Block is accepted; XCH reward is paid to the attacker.
```

For Path B (unsolicited), the attacker skips steps 2–4 entirely and sends `RespondSignatures` directly for any `sp_hash` currently in `farmer.sps`, bypassing even the logging-only check.

### Citations

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
