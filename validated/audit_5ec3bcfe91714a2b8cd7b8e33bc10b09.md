### Title
Harvester `farmer_reward_address_override` in `RespondSignatures` Accepted Without Fee Quality Enforcement, Enabling Unconditional Block Reward Diversion — (File: `chia/farmer/farmer_api.py`)

---

### Summary

The farmer's `_process_respond_signatures()` method unconditionally accepts `farmer_reward_address_override` from a harvester's `RespondSignatures` message and uses it as the block reward destination in `DeclareProofOfSpace`, without enforcing the CHIP-22 fee quality threshold. A malicious connected harvester can redirect 100% of the farmer's block rewards (XCH) to an arbitrary address.

---

### Finding Description

CHIP-22 defines a fee convention where third-party harvesters can take a fee by setting `farmer_reward_address_override` in their protocol messages. The convention requires the harvester to meet a fee quality threshold (derived from the proof and challenge hash) to legitimately redirect the reward. However, the farmer's enforcement of this threshold is purely advisory and incomplete.

**Issue 1 — Advisory-only check in `new_proof_of_space()`:**

When a harvester sends `NewProofOfSpace` with `farmer_reward_address_override` set, the farmer calls `notify_farmer_reward_taken_by_harvester_as_fee()`: [1](#0-0) 

That function checks the fee quality but only emits log warnings — it does not reject the override, raise an exception, or halt the block-making flow: [2](#0-1) 

**Issue 2 — No check at all in `_process_respond_signatures()`:**

When the harvester later sends `RespondSignatures`, the farmer unconditionally substitutes the override as the block reward address: [3](#0-2) 

This override is then embedded in `DeclareProofOfSpace` sent to the full node: [4](#0-3) 

The `farmer_reward_address_override` field in `RespondSignatures` is **not part of the BLS-signed data** — it is a plain protocol field accepted without authentication: [5](#0-4) 

A malicious harvester can therefore:
1. Send `NewProofOfSpace` with `farmer_reward_address_override=None` (bypassing even the advisory log check).
2. Respond to `RequestSignatures` with `RespondSignatures` containing `farmer_reward_address_override` set to an attacker-controlled puzzle hash.
3. The farmer uses that address in `DeclareProofOfSpace`, and the full node creates a block paying the block reward to the attacker.

---

### Impact Explanation

Every block won while the farmer is connected to the malicious harvester results in the farmer's XCH block reward being permanently redirected to the attacker's address. This is a direct, irreversible loss of XCH with zero cryptographic cost to the attacker. The farmer has no automated enforcement mechanism — the only signal is a log warning that may go unnoticed.

This falls under: **High — payout redirection enabling unauthorized coin control of XCH block rewards.**

---

### Likelihood Explanation

Any harvester that successfully connects to the farmer can exploit this. Third-party harvester services (the primary use case for CHIP-22) are operated by entities separate from the farmer. The attack is silent — the farmer's logs show a warning only if `farmer_reward_address_override` was also set in `NewProofOfSpace`, but the attacker can avoid that by setting it only in `RespondSignatures`. The farmer has no automated disconnect or enforcement logic.

---

### Recommendation

In `_process_respond_signatures()`, before applying `farmer_reward_address_override` from `RespondSignatures`, enforce the fee quality threshold:

```python
if response.farmer_reward_address_override is not None:
    fee_quality = calculate_harvester_fee_quality(pospace.proof, response.challenge_hash)
    # Retrieve fee_info from the stored NewProofOfSpace for this sp_hash/plot_identifier
    if fee_info is None or fee_quality > fee_info.applied_fee_threshold:
        self.farmer.log.error("Rejecting farmer_reward_address_override: fee quality threshold not met")
        return None  # Drop the block rather than redirect the reward
    farmer_reward_address = response.farmer_reward_address_override
```

Additionally, `notify_farmer_reward_taken_by_harvester_as_fee()` should return a boolean and its caller in `new_proof_of_space()` should abort the signing flow when the threshold is not met, rather than proceeding.

---

### Proof of Concept

A malicious third-party harvester:

1. Connects to the farmer normally and finds a valid proof of space.
2. Sends `NewProofOfSpace` with `farmer_reward_address_override=None` — no fee notification, no advisory log.
3. Farmer sends `RequestSignatures` back to the harvester.
4. Harvester responds with a valid `RespondSignatures` (correct BLS signatures over `challenge_chain_sp` / `reward_chain_sp`) but with `farmer_reward_address_override=attacker_puzzle_hash`.
5. `_process_respond_signatures()` at line 917–918 substitutes `attacker_puzzle_hash` for `self.farmer.farmer_target` with no validation.
6. `DeclareProofOfSpace` is broadcast to the full node with `farmer_reward_puzzle_hash = attacker_puzzle_hash`.
7. The block is accepted; the XCH block reward is paid to the attacker.

No cryptographic break is required. The `farmer_reward_address_override` field is unsigned and accepted verbatim. [6](#0-5) [7](#0-6)

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
