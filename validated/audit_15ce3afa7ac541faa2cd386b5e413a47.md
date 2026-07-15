The code path is concrete and traceable. Here is the analysis:

---

### Title
Malicious Harvester Can Redirect Farmer Block Rewards via Unvalidated `farmer_reward_address_override` in `RespondSignatures` — (`chia/farmer/farmer_api.py`)

### Summary

`_process_respond_signatures` unconditionally trusts the `farmer_reward_address_override` field in a harvester-supplied `RespondSignatures` message and uses it as the `farmer_reward_puzzle_hash` in the `DeclareProofOfSpace` broadcast to the full node. There is no validation that this field matches what the harvester declared in the earlier `NewProofOfSpace` message, and no farmer-side guard rejects or caps the override. A malicious harvester can silently redirect the farmer's block reward to an attacker-controlled puzzle hash.

### Finding Description

**Protocol message definition — `RespondSignatures` carries the override field:**

`RespondSignatures` (the harvester→farmer reply to `RequestSignatures`) includes `farmer_reward_address_override: bytes32 | None` as a first-class streamable field. [1](#0-0) 

**The farmer blindly applies it:**

In `_process_respond_signatures`, the farmer sets `farmer_reward_address` to its own configured `farmer_target`, then immediately overwrites it with whatever the harvester sent — with no validation:

```python
farmer_reward_address = self.farmer.farmer_target
if response.farmer_reward_address_override is not None:
    farmer_reward_address = response.farmer_reward_address_override
    include_source_signature_data = True
``` [2](#0-1) 

That address is then passed directly into `DeclareProofOfSpace` as `farmer_reward_puzzle_hash`: [3](#0-2) 

**The notification guard is in the wrong message and is non-blocking:**

The only farmer-side awareness of the override is a notification call triggered by `NewProofOfSpace.farmer_reward_address_override`: [4](#0-3) 

This notification is:
1. Triggered by the `NewProofOfSpace` message, **not** by `RespondSignatures`.
2. Purely informational — it does not block the flow or store the value for later cross-checking.

**The two fields are never correlated.** `RequestSignatures` carries no `farmer_reward_address_override` field: [5](#0-4) 

So the farmer has no record of what override (if any) was declared in `NewProofOfSpace` when it later processes `RespondSignatures`.

### Impact Explanation

A malicious harvester executes the following:

1. Sends `NewProofOfSpace` with `farmer_reward_address_override=None` — the notification is **not** triggered, so the farmer has no awareness.
2. Receives `RequestSignatures` from the farmer (normal flow).
3. Replies with `RespondSignatures` where `farmer_reward_address_override=attacker_puzzle_hash`.
4. The farmer's `_process_respond_signatures` overwrites `farmer_target` with the attacker's address and broadcasts `DeclareProofOfSpace` to the full node with `farmer_reward_puzzle_hash=attacker_puzzle_hash`.
5. When the block is accepted, the 0.25 XCH farmer reward coin is created at the attacker's address, not the farmer's.

Impact: **unauthorized XCH reward diversion** — a Critical/High impact under the allowed scope (reward diversion affecting XCH).

### Likelihood Explanation

Third-party harvesters (e.g., DrPlotter, GPU harvesters) are explicitly supported via CHIP-22 and are a common deployment pattern. Any such service that turns malicious — or any harvester peer that an operator connects to — can silently steal every block reward the farmer wins. The farmer sees normal log output and no error; the only observable effect is that rewards arrive at the wrong address on-chain.

### Recommendation

- When processing `RespondSignatures`, **ignore** `farmer_reward_address_override` entirely and always use `self.farmer.farmer_target`. The override, if it is to be supported at all, must be validated against the value declared in the corresponding `NewProofOfSpace` (which must be stored at cache time).
- Alternatively, store the `farmer_reward_address_override` from `NewProofOfSpace` alongside the cached proof in `self.farmer.proofs_of_space`, and in `_process_respond_signatures` assert that `response.farmer_reward_address_override == cached_override`.
- Add a farmer configuration option to disable `farmer_reward_address_override` entirely for operators who do not use third-party harvesters.

### Proof of Concept

```python
# Pseudocode unit test
farmer_target = bytes32(b"\x00" * 32)          # legitimate farmer address
attacker_ph   = bytes32(b"\xff" * 32)          # attacker-controlled address

# Step 1: harvester sends NewProofOfSpace with override=None (no notification)
new_pos = NewProofOfSpace(..., farmer_reward_address_override=None)
await farmer_api.new_proof_of_space(new_pos, mock_peer)

# Step 2: farmer sends RequestSignatures (normal)
# Step 3: harvester replies with RespondSignatures carrying attacker address
respond_sig = RespondSignatures(
    ...,
    farmer_reward_address_override=attacker_ph,
    message_signatures=[valid_cc_sp_sig, valid_rc_sp_sig],
)
result = farmer_api._process_respond_signatures(respond_sig)

assert isinstance(result, DeclareProofOfSpace)
# This assertion FAILS — reward goes to attacker, not farmer
assert result.farmer_reward_puzzle_hash == farmer_target
```

The assertion fails because `result.farmer_reward_puzzle_hash == attacker_ph`.

### Citations

**File:** chia/protocols/harvester_protocol.py (L119-126)
```python
class RequestSignatures(Streamable):
    plot_identifier: str
    challenge_hash: bytes32
    sp_hash: bytes32
    messages: list[bytes32]
    # This, and rc_block_unfinished are only set when using a third-party harvester (see CHIP-22)
    message_data: list[SignatureRequestSourceData | None] | None
    rc_block_unfinished: RewardChainBlockUnfinished | None
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

**File:** chia/farmer/farmer_api.py (L128-129)
```python
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
