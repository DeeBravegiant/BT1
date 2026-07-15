The code path is fully traced. Here is the analysis:

---

### Title
Farmer Signs Attacker-Controlled Foliage Hashes Without Reward Address Verification — (`chia/farmer/farmer_api.py`)

### Summary

`request_signed_values` in `FarmerAPI` accepts `foliage_block_data_hash` and `foliage_transaction_block_hash` directly from a connected full node peer and forwards them to the harvester for signing with no check that the embedded `farmer_reward_puzzle_hash` matches the farmer's configured reward address. A malicious full node can redirect the farmer's block reward to an attacker-controlled address.

### Finding Description

The `request_signed_values` handler performs exactly one guard: it checks that `full_node_request.quality_string` exists in `self.farmer.quality_str_to_identifiers`. [1](#0-0) 

After that single check, the handler blindly forwards the full node's `foliage_block_data_hash` and `foliage_transaction_block_hash` to the harvester: [2](#0-1) 

`_process_respond_signatures` then co-signs those hashes with the farmer's private key and returns `SignedValues`: [3](#0-2) 

There is no point in this path where the farmer verifies:
- that `foliage_block_data.farmer_reward_puzzle_hash == self.farmer.farmer_target`, or
- that `foliage_block_data.get_hash() == foliage_block_data_hash` (even when `foliage_block_data` is optionally provided).

On the full node side, `declare_proof_of_space` sets `farmer_ph = request.farmer_puzzle_hash` (the value the farmer declared) and stores a candidate block: [4](#0-3) [5](#0-4) 

A malicious full node ignores the farmer's declared `farmer_puzzle_hash` and instead stores a candidate block whose `FoliageBlockData.farmer_reward_puzzle_hash` is the attacker's address. It then sends `RequestSignedValues` with the hash of that attacker-controlled foliage block data.

When `signed_values` is later received by the malicious full node, it verifies the signature against its locally stored candidate: [6](#0-5) 

Because the candidate was constructed with the attacker's reward address, and the farmer signed the hash of that exact candidate, the verification passes and the block is finalized with the attacker's `farmer_reward_puzzle_hash`.

The test suite itself demonstrates awareness of this invariant — it asserts `farmer_reward_puzzle_hash == farmer_reward_address` in an interceptor — but this check exists only in test code, not in production: [7](#0-6) 

### Impact Explanation

A malicious full node can steal every block reward earned by any farmer connected to it. The farmer's plot key signs a foliage block data structure whose `farmer_reward_puzzle_hash` is the attacker's address. The resulting block is consensus-valid and will be accepted by the entire network. The farmer receives no reward and has no indication anything went wrong.

### Likelihood Explanation

- Farmers who connect to third-party or pool-operated full nodes are directly exposed.
- Pool operators already have an established trust relationship with farmers, making this a realistic insider-threat vector.
- The attack is completely silent: the farmer's logs show a normal block win.
- No leaked keys, broken crypto, or social engineering is required — only a TCP connection from the farmer to the malicious full node.

### Recommendation

In `request_signed_values`, before forwarding hashes to the harvester, the farmer must verify the reward address:

```python
if full_node_request.foliage_block_data is not None:
    # Verify hash consistency
    if full_node_request.foliage_block_data.get_hash() != full_node_request.foliage_block_data_hash:
        self.farmer.log.error("foliage_block_data hash mismatch")
        return None
    # Verify farmer reward address
    if full_node_request.foliage_block_data.farmer_reward_puzzle_hash != self.farmer.farmer_target:
        self.farmer.log.error("foliage_block_data contains wrong farmer reward address")
        return None
else:
    # foliage_block_data is optional but without it the farmer cannot verify the reward address
    # Reject or require it to always be present
    self.farmer.log.error("foliage_block_data missing; cannot verify farmer reward address")
    return None
```

The `foliage_block_data` field should be made mandatory (non-optional) in `RequestSignedValues` so the farmer can always perform this check. [8](#0-7) 

### Proof of Concept

1. Run a patched full node that, in `declare_proof_of_space`, replaces `farmer_ph` with `attacker_puzzle_hash` before calling `create_unfinished_block` and `add_candidate_block`.
2. Connect a legitimate farmer to this full node.
3. Wait for the farmer to win a block (valid quality string registered in `quality_str_to_identifiers`).
4. The malicious full node sends `RequestSignedValues(quality_string=qs, foliage_block_data_hash=hash(attacker_foliage_block_data), ...)`.
5. The farmer's `request_signed_values` passes the quality string check and forwards the hash to the harvester.
6. The harvester and farmer co-sign the attacker's hash; `SignedValues` is returned.
7. The malicious full node's `signed_values` handler verifies the signature against its locally stored candidate (which has `farmer_reward_puzzle_hash = attacker_puzzle_hash`) — verification passes.
8. `add_unfinished_block` is called; the block propagates to the network with the attacker's reward address.
9. Differential test: compare `candidate.foliage.foliage_block_data.farmer_reward_puzzle_hash` in the finalized block against `farmer.farmer_target` — they differ.

### Citations

**File:** chia/farmer/farmer_api.py (L722-730)
```python
    @metadata.request()
    async def request_signed_values(self, full_node_request: farmer_protocol.RequestSignedValues) -> Message | None:
        if full_node_request.quality_string not in self.farmer.quality_str_to_identifiers:
            self.farmer.log.error(f"Do not have quality string {full_node_request.quality_string}")
            return None

        (plot_identifier, challenge_hash, sp_hash, node_id) = self.farmer.quality_str_to_identifiers[
            full_node_request.quality_string
        ]
```

**File:** chia/farmer/farmer_api.py (L749-756)
```python
        request = harvester_protocol.RequestSignatures(
            plot_identifier,
            challenge_hash,
            sp_hash,
            [full_node_request.foliage_block_data_hash, full_node_request.foliage_transaction_block_hash],
            message_data=message_data,
            rc_block_unfinished=full_node_request.rc_block_unfinished,
        )
```

**File:** chia/farmer/farmer_api.py (L959-979)
```python
                    foliage_sig_farmer = AugSchemeMPL.sign(sk, foliage_block_data_hash, agg_pk)
                    foliage_transaction_block_sig_farmer = AugSchemeMPL.sign(sk, foliage_transaction_block_hash, agg_pk)

                    foliage_agg_sig = AugSchemeMPL.aggregate(
                        [foliage_sig_harvester, foliage_sig_farmer, foliage_sig_taproot]
                    )
                    foliage_block_agg_sig = AugSchemeMPL.aggregate(
                        [
                            foliage_transaction_block_sig_harvester,
                            foliage_transaction_block_sig_farmer,
                            foliage_transaction_block_sig_taproot,
                        ]
                    )
                    assert AugSchemeMPL.verify(agg_pk, foliage_block_data_hash, foliage_agg_sig)
                    assert AugSchemeMPL.verify(agg_pk, foliage_transaction_block_hash, foliage_block_agg_sig)

                    return farmer_protocol.SignedValues(
                        computed_quality_string,
                        foliage_agg_sig,
                        foliage_block_agg_sig,
                    )
```

**File:** chia/full_node/full_node_api.py (L1067-1069)
```python
                farmer_ph = self.full_node.constants.GENESIS_PRE_FARM_FARMER_PUZZLE_HASH
            else:
                farmer_ph = request.farmer_puzzle_hash
```

**File:** chia/full_node/full_node_api.py (L1168-1170)
```python
            self.full_node.full_node_store.add_candidate_block(quality_string, height, unfinished_block)

            foliage_sb_data_hash = unfinished_block.foliage.foliage_block_data.get_hash()
```

**File:** chia/full_node/full_node_api.py (L1242-1248)
```python
        if not AugSchemeMPL.verify(
            candidate.reward_chain_block.proof_of_space.plot_public_key,
            candidate.foliage.foliage_block_data.get_hash(),
            farmer_request.foliage_block_data_signature,
        ):
            self.log.warning("Signature not valid. There might be a collision in plots. Ignore this during tests.")
            return None
```

**File:** chia/_tests/farmer_harvester/test_third_party_harvesters.py (L235-244)
```python
        assert request.foliage_block_data
        assert request.foliage_block_data.get_hash() == request.foliage_block_data_hash
        assert request.foliage_transaction_block_data
        assert request.foliage_transaction_block_data.get_hash() == request.foliage_transaction_block_hash

        assert (
            request.foliage_block_data.farmer_reward_puzzle_hash == farmer_reward_address
            or request.foliage_block_data.farmer_reward_puzzle_hash
            == bytes32(full_node_2.constants.GENESIS_PRE_FARM_FARMER_PUZZLE_HASH)
        )
```

**File:** chia/protocols/farmer_protocol.py (L80-87)
```python
class RequestSignedValues(Streamable):
    quality_string: bytes32
    foliage_block_data_hash: bytes32
    foliage_transaction_block_hash: bytes32
    foliage_block_data: FoliageBlockData | None = None
    foliage_transaction_block_data: FoliageTransactionBlock | None = None
    rc_block_unfinished: RewardChainBlockUnfinished | None = None

```
