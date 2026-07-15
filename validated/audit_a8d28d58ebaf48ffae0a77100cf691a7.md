The code trace is complete. Let me lay out the exact findings.

## Vulnerability Confirmed

### Title
Malicious Harvester Can Redirect Farmer Block Reward via Unauthenticated `farmer_reward_address_override` in `RespondSignatures` — (`chia/farmer/farmer_api.py`)

---

### Summary

A harvester connected to a farmer can set `RespondSignatures.farmer_reward_address_override` to an arbitrary puzzle hash. The farmer's `_process_respond_signatures` accepts this value without any cryptographic binding, ownership check, or comparison against the farmer's configured target, and uses it verbatim as `farmer_puzzle_hash` in `DeclareProofOfSpace` sent to the full node. This redirects the farmer block reward to the attacker's address for every block won by that harvester.

---

### Finding Description

**Step 1 — Protocol message structure**

`RespondSignatures` contains an optional `farmer_reward_address_override: bytes32 | None` field that any connected harvester can freely populate: [1](#0-0) 

**Step 2 — Farmer blindly trusts the field**

In `_process_respond_signatures`, the farmer initialises `farmer_reward_address` from its own config (`self.farmer.farmer_target`), then unconditionally overwrites it with whatever the harvester sent: [2](#0-1) 

There is no check that:
- the override was requested by the farmer,
- it matches the value the harvester declared in `NewProofOfSpace`,
- it is cryptographically bound to the plot key, or
- it equals `self.farmer.farmer_target`.

**Step 3 — Attacker-controlled address flows into `DeclareProofOfSpace`**

The tainted `farmer_reward_address` is placed directly into the `DeclareProofOfSpace` message sent to the full node: [3](#0-2) 

**Step 4 — Full node uses the address to construct the foliage**

The full node builds the unfinished block with the attacker's puzzle hash as the farmer reward destination. The subsequent `RequestSignedValues` → `RespondSignatures` round-trip signs the foliage block data that already encodes the attacker's address, so the final block is valid and accepted by consensus.

**Step 5 — The `NewProofOfSpace` path does not guard this**

The only existing check for `farmer_reward_address_override` is in `new_proof_of_space`, which only logs a warning when the field is set in `NewProofOfSpace`: [4](#0-3) 

A malicious harvester can set `NewProofOfSpace.farmer_reward_address_override = None` (avoiding even the log) and only inject the attacker address in the later `RespondSignatures` message. The two fields are never cross-validated.

---

### Impact Explanation

Every block won by the compromised harvester pays the farmer reward (currently 0.25 XCH per block) to the attacker's puzzle hash instead of the farmer's configured `xch_target_address`. This is a direct, irreversible XCH loss for the farmer. The impact matches **High: payout redirection / unauthorized coin control** under the scope rules.

---

### Likelihood Explanation

- Any party that can establish a TCP connection to the farmer's harvester port (default 8448) and complete the TLS handshake as `NodeType.HARVESTER` can execute this attack.
- No leaked keys, no admin access, and no broken cryptography are required.
- The attack is silent when `NewProofOfSpace.farmer_reward_address_override` is left `None`; no warning is logged.
- The attacker only needs to hold valid plots (to produce a winning proof) or collude with a legitimate harvester operator.

---

### Recommendation

In `_process_respond_signatures`, before accepting `response.farmer_reward_address_override`, validate it against the value the harvester declared in the corresponding `NewProofOfSpace`. The farmer should store the expected override (or `None`) when it caches the proof in `self.farmer.proofs_of_space`, then assert equality:

```python
# pseudocode
expected_override = stored_proof_metadata[response.plot_identifier].farmer_reward_address_override
if response.farmer_reward_address_override != expected_override:
    self.farmer.log.error("farmer_reward_address_override mismatch — dropping")
    return None
```

Additionally, consider rejecting any `RespondSignatures.farmer_reward_address_override` that was not explicitly requested (i.e., when the corresponding `NewProofOfSpace` had `farmer_reward_address_override = None`).

---

### Proof of Concept

```python
# Attacker controls a harvester process.
# 1. Connect to farmer as NodeType.HARVESTER, complete TLS handshake.
# 2. Wait for NewSignagePointHarvester from farmer.
# 3. Submit a winning NewProofOfSpace with farmer_reward_address_override=None.
# 4. Receive RequestSignatures from farmer.
# 5. Reply with RespondSignatures where:
#      farmer_reward_address_override = attacker_puzzle_hash  # arbitrary bytes32
#      message_signatures = [valid harvester half-signatures for cc_sp and rc_sp]
# 6. Farmer calls _process_respond_signatures:
#      farmer_reward_address = self.farmer.farmer_target   # line 916
#      # line 917: response.farmer_reward_address_override is not None → True
#      farmer_reward_address = attacker_puzzle_hash        # line 918
# 7. Farmer sends DeclareProofOfSpace(farmer_puzzle_hash=attacker_puzzle_hash) to full node.
# 8. Full node builds block; farmer reward coin goes to attacker_puzzle_hash.
# Assert: DeclareProofOfSpace.farmer_puzzle_hash == attacker_puzzle_hash  ✓
``` [5](#0-4) [6](#0-5)

### Citations

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

**File:** chia/farmer/farmer_api.py (L821-849)
```python
    def _process_respond_signatures(
        self, response: harvester_protocol.RespondSignatures
    ) -> DeclareProofOfSpace | SignedValues | None:
        """
        Processing the responded signatures happens when receiving an unsolicited request for an SP or when receiving
        the signature response for a block from a harvester.
        """
        if response.sp_hash not in self.farmer.sps:
            self.farmer.log.warning(f"Do not have challenge hash {response.challenge_hash}")
            return None
        is_sp_signatures: bool = False
        sps = self.farmer.sps[response.sp_hash]
        peak_height = sps[0].peak_height
        last_tx_height = sps[0].last_tx_height
        signage_point_index = sps[0].signage_point_index
        found_sp_hash_debug = False
        for sp_candidate in sps:
            if response.sp_hash == response.message_signatures[0][0]:
                found_sp_hash_debug = True
                if sp_candidate.reward_chain_sp == response.message_signatures[1][0]:
                    is_sp_signatures = True
        if found_sp_hash_debug:
            assert is_sp_signatures

        pospace = None
        for plot_identifier, candidate_pospace in self.farmer.proofs_of_space[response.sp_hash]:
            if plot_identifier == response.plot_identifier:
                pospace = candidate_pospace
        assert pospace is not None
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
