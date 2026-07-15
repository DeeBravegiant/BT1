### Title
Harvester Can Unconditionally Redirect Farmer Block Reward via Unvalidated `farmer_reward_address_override` in `RespondSignatures` — (File: `chia/farmer/farmer_api.py`)

---

### Summary

The `_process_respond_signatures()` function in `FarmerAPI` unconditionally accepts the `farmer_reward_address_override` field from a harvester's `RespondSignatures` message and uses it as the `farmer_puzzle_hash` in `DeclareProofOfSpace`, redirecting the farmer's block reward (XCH) to any arbitrary puzzle hash. No fee quality enforcement, no cryptographic proof, and no cross-message consistency check is performed. The only "check" — a log warning in `notify_farmer_reward_taken_by_harvester_as_fee()` — is called from a different code path (`new_proof_of_space`) and is never invoked during `_process_respond_signatures()`.

---

### Finding Description

**Vulnerability class**: Auth bypass / payout redirection — a privileged action (setting the farmer reward destination) can be performed by an unprivileged party (a connected harvester) with no authorization guard.

**Root cause in `_process_respond_signatures()`**:

```python
farmer_reward_address = self.farmer.farmer_target
if response.farmer_reward_address_override is not None:
    farmer_reward_address = response.farmer_reward_address_override   # ← no check
    include_source_signature_data = True
``` [1](#0-0) 

This override is then passed directly as `farmer_puzzle_hash` in the `DeclareProofOfSpace` message sent to the full node:

```python
return farmer_protocol.DeclareProofOfSpace(
    ...
    farmer_reward_address,   # ← attacker-controlled
    ...
)
``` [2](#0-1) 

The `RespondSignatures` protocol message explicitly carries this field with no restrictions:

```python
class RespondSignatures(Streamable):
    ...
    farmer_reward_address_override: bytes32 | None
``` [3](#0-2) 

**The CHIP-22 fee quality check is never called in this path.** The function `notify_farmer_reward_taken_by_harvester_as_fee()` is only invoked from `new_proof_of_space()` (line 128–129), and even there it only **logs** a warning — it does not reject the override or abort the block-making flow: [4](#0-3) [5](#0-4) 

A harvester can bypass even that logging check entirely by sending `NewProofOfSpace` without an override, then injecting `farmer_reward_address_override` only in the subsequent `RespondSignatures` — `_process_respond_signatures()` never calls `notify_farmer_reward_taken_by_harvester_as_fee()`.

---

### Impact Explanation

**High — Unauthorized payout redirection of XCH block rewards.**

Every block won by the farmer while a malicious harvester is connected can have its farmer reward (currently 0.25 XCH per block) permanently redirected to an attacker-controlled puzzle hash. The farmer receives nothing. The loss is irreversible once the block is confirmed on-chain.

---

### Likelihood Explanation

CHIP-22 explicitly introduces third-party harvesters as a supported deployment model. Any third-party harvester operator — who controls the harvester software — can exploit this without any additional privileges. The harvester is already a connected, authenticated peer to the farmer (TLS), but that authentication does not authorize it to redirect the farmer's reward address. The attack requires only that the harvester win at least one block, which is the normal expected operation.

---

### Recommendation

1. **Enforce the fee quality convention as a hard gate, not a log.** In `_process_respond_signatures()`, before accepting `farmer_reward_address_override`, compute `calculate_harvester_fee_quality` and reject the override (return `None` or revert to `self.farmer.farmer_target`) if `fee_quality > applied_fee_threshold`.

2. **Cross-check consistency.** Verify that the `farmer_reward_address_override` in `RespondSignatures` matches the one declared in the corresponding `NewProofOfSpace` message (stored in `self.farmer.proofs_of_space`). A mismatch should be treated as a protocol violation.

3. **Call `notify_farmer_reward_taken_by_harvester_as_fee()` from `_process_respond_signatures()`** (not only from `new_proof_of_space()`), and make it return a boolean indicating whether the override is legitimate, so the caller can act on it.

---

### Proof of Concept

1. A malicious third-party harvester connects to the farmer over the standard harvester protocol.
2. The harvester sends a valid `NewProofOfSpace` (with `farmer_reward_address_override=None`) for a winning proof.
3. The farmer sends `RequestSignatures` back to the harvester.
4. The harvester responds with `RespondSignatures` where `farmer_reward_address_override = attacker_puzzle_hash` (any `bytes32`).
5. `FarmerAPI._process_respond_signatures()` executes lines 916–918: `farmer_reward_address` is set to `attacker_puzzle_hash` with no validation.
6. The farmer broadcasts `DeclareProofOfSpace` with `farmer_puzzle_hash = attacker_puzzle_hash` to the full node.
7. The full node creates an unfinished block paying the farmer reward to `attacker_puzzle_hash`.
8. The block is finalized; the farmer's XCH reward is permanently lost to the attacker.

### Citations

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
