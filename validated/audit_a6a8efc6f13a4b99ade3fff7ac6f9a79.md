Looking at the actual code in `chia/farmer/farmer_api.py`: [1](#0-0) 

The override is accepted unconditionally — no allowlist check, no signature over the override value, no configuration gate. The attacker-supplied `bytes32` flows directly into `DeclareProofOfSpace.farmer_puzzle_hash`.

The full attack chain is self-consistent:

1. Malicious harvester sends `RespondSignatures(farmer_reward_address_override=attacker_ph)` for the SP-signature phase.
2. `_process_respond_signatures` replaces `self.farmer.farmer_target` with `attacker_ph` and returns `DeclareProofOfSpace(farmer_puzzle_hash=attacker_ph)`.
3. The full node builds foliage committing to `attacker_ph` as the farmer reward address and sends `RequestSignedValues` back to the farmer.
4. The farmer forwards `RequestSignatures` (with the foliage block data hash) to the harvester.
5. The malicious harvester signs that hash — it is happy to, because the hash commits to its own address.
6. The farmer also signs the foliage hash without inspecting what address it commits to (lines 959–960).
7. The block is broadcast with the farmer reward locked to `attacker_ph`. [2](#0-1) 

The farmer's own signing step at the block-signature phase does not re-validate that the foliage commits to `self.farmer.farmer_target`, so the farmer's key co-signs a block that pays the attacker.

`notify_farmer_reward_taken_by_harvester_as_fee` is a notification/logging hook; it does not block or revert the override. [3](#0-2) 

---

### Title
Malicious harvester can redirect farmer block reward to arbitrary address via unchecked `farmer_reward_address_override` — (`chia/farmer/farmer_api.py`)

### Summary
Any harvester connected to a farmer can set `RespondSignatures.farmer_reward_address_override` to an arbitrary puzzle hash. The farmer accepts this value without validation and broadcasts `DeclareProofOfSpace` with the attacker's address as `farmer_puzzle_hash`, permanently redirecting the block reward for every block won while the attacker's harvester is connected.

### Finding Description
In `_process_respond_signatures` (farmer_api.py line 821), the farmer initialises `farmer_reward_address = self.farmer.farmer_target` and then unconditionally replaces it with `response.farmer_reward_address_override` if the field is non-`None` (lines 916–919). No check verifies that the override equals the farmer's configured address, that the harvester is authorised to use this field, or that the override is accompanied by any cryptographic proof of ownership. The overridden address is passed directly to `DeclareProofOfSpace` (line 929) and forwarded to the full node, which uses it to construct the foliage. The harvester then signs the foliage block data hash (which commits to the attacker's address), and the farmer co-signs it without inspecting the committed address (lines 959–960). The result is a fully valid block paying the farmer reward to the attacker.

### Impact Explanation
Every block won while the attacker's harvester is connected pays the farmer reward (currently 0.25 XCH per block) to the attacker's address. The farmer receives nothing. This is an unauthorized XCH reward diversion — Critical impact under the allowed scope.

### Likelihood Explanation
The precondition is that the attacker controls a harvester connected to the farmer. This is realistic in the third-party harvester model (e.g., a farmer outsources plot storage to a service). The farmer operator may not expect that connecting a harvester grants it the ability to redirect block rewards. No special network position or key material is required beyond the existing harvester TLS connection.

### Recommendation
- Reject `farmer_reward_address_override` values that do not equal `self.farmer.farmer_target` unless the farmer has explicitly opted in (e.g., via a per-peer allowlist in config).
- Alternatively, add a configuration flag `allow_farmer_reward_address_override: bool` (default `false`) and return `None` / log an error when the flag is unset and an override is received.
- At minimum, require the harvester to provide a BLS signature over `(farmer_reward_address_override || challenge_hash)` using a key the farmer has pre-approved, so the override cannot be injected without prior authorisation.

### Proof of Concept
Patch a harvester's `RespondSignatures` construction to always set `farmer_reward_address_override = attacker_bytes32`. Connect this harvester to a farmer that has a winning proof for the current SP. Assert that the resulting `DeclareProofOfSpace.farmer_puzzle_hash == attacker_bytes32 != farmer.farmer_target`. The existing test file `chia/_tests/farmer_harvester/test_third_party_harvesters.py` already exercises this code path and can be adapted to confirm the redirect with a hostile address.

### Citations

**File:** chia/farmer/farmer_api.py (L821-823)
```python
    def _process_respond_signatures(
        self, response: harvester_protocol.RespondSignatures
    ) -> DeclareProofOfSpace | SignedValues | None:
```

**File:** chia/farmer/farmer_api.py (L916-933)
```python
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

**File:** chia/farmer/farmer_api.py (L959-960)
```python
                    foliage_sig_farmer = AugSchemeMPL.sign(sk, foliage_block_data_hash, agg_pk)
                    foliage_transaction_block_sig_farmer = AugSchemeMPL.sign(sk, foliage_transaction_block_hash, agg_pk)
```
