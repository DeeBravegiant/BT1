### Title
Unauthenticated `sub_epoch_summary` Injection via `NewPeakTimelord` Causes Timelord to Broadcast Invalid `EndOfSubSlotBundle` — (`chia/timelord/timelord_state.py`, `chia/timelord/timelord_api.py`, `chia/timelord/timelord.py`)

---

### Summary

The `new_peak_timelord` handler accepts the `sub_epoch_summary` field of a `NewPeakTimelord` message from any connected full-node peer without any validation against the canonical chain. This field is stored verbatim in `LastState` and, when the sub-epoch boundary conditions are met, is embedded directly into the `ChallengeChainSubSlot` of the next `EndOfSubSlotBundle`. Honest full nodes validate the SES hash against the canonical chain's computed value and reject bundles with a wrong hash, causing the timelord to be forked off the canonical chain and unable to contribute valid EOS bundles.

---

### Finding Description

**Step 1 — Entry point: `new_peak_timelord` handler**

Any connected full-node peer can send a `NewPeakTimelord` message. The handler in `timelord_api.py` performs only weight-based comparisons: [1](#0-0) 

There is no validation of the `sub_epoch_summary` field. If the incoming peak has higher weight (or equal weight with lower iters), it is accepted and stored as `self.timelord.new_peak`.

**Step 2 — Unvalidated storage in `set_state`**

`_handle_new_peak` calls `self.last_state.set_state(self.new_peak)`, which blindly stores the peer-supplied SES: [2](#0-1) 

No cross-check against the blockchain is performed.

**Step 3 — `get_next_sub_epoch_summary` returns the attacker-controlled value**

When `passed_ses_height_but_not_yet_included` is `True` and `deficit == 0`, the stored (fake) SES is returned: [3](#0-2) 

**Step 4 — Fake SES embedded into `EndOfSubSlotBundle`**

In `_check_for_end_of_subslot`, the returned SES is used to populate `ses_hash`, `new_sub_slot_iters`, and `new_difficulty`, which are embedded into `ChallengeChainSubSlot` and then the `EndOfSubSlotBundle`: [4](#0-3) 

The bundle is then broadcast to all connected full nodes: [5](#0-4) 

**Step 5 — Full nodes reject the bundle**

Full-node block header validation independently computes the expected SES using `make_sub_epoch_summary` and rejects any bundle whose `subepoch_summary_hash` does not match: [6](#0-5) 

**The `NewPeakTimelord` protocol message structure**

The `sub_epoch_summary` field is a plain optional field with no cryptographic binding to the `reward_chain_block`: [7](#0-6) 

**How the legitimate full node constructs this field**

The honest full node computes the SES from the canonical blockchain state before sending: [8](#0-7) 

A malicious peer skips this computation and supplies an arbitrary `SubEpochSummary`.

---

### Impact Explanation

The timelord computes valid VDFs (the VDF proofs themselves are correct) but embeds a wrong `subepoch_summary_hash` in the `ChallengeChainSubSlot`. Every honest full node rejects the resulting `EndOfSubSlotBundle` with `INVALID_SUB_EPOCH_SUMMARY`. The timelord is effectively forked off the canonical chain for the duration of the attack. If the attacker supplies a `reward_chain_block` with weight `uint128::MAX`, the timelord will never switch back to the canonical chain without a manual restart, causing a permanent inability to produce valid EOS bundles. This matches **High: Permanent or long-lived inability for honest timelords to process valid blocks under normal network assumptions**.

---

### Likelihood Explanation

Public timelords (e.g., those listed in the Chia network) accept inbound connections from any full-node peer. The `on_connect` callback is a no-op with no peer authentication: [9](#0-8) 

An attacker only needs to:
1. Connect to the timelord's WebSocket port (default 8446)
2. Send a well-formed `NewPeakTimelord` with a real `reward_chain_block` (observed from the network) at a sub-epoch boundary, but with a fabricated `sub_epoch_summary`

No keys, no PoW, no special privileges are required.

---

### Recommendation

Before storing `state.sub_epoch_summary` in `set_state`, the timelord should independently recompute the expected SES using `make_sub_epoch_summary` (or an equivalent function) against its own view of the canonical chain, and reject any `NewPeakTimelord` whose `sub_epoch_summary` does not match. Alternatively, the timelord should only accept `NewPeakTimelord` messages from a single, explicitly configured trusted full-node peer (enforced at the connection layer, not just by firewall convention).

---

### Proof of Concept

```python
# Attacker observes a real block at sub-epoch boundary (height H where
# (H+1) % SUB_EPOCH_BLOCKS == 0) from the network.
real_rcb = observed_block.reward_chain_block  # valid, higher weight than timelord's current peak

# Craft a fake SubEpochSummary with wrong prev_ses_hash
fake_ses = SubEpochSummary(
    prev_subepoch_summary_hash=bytes32(b"\xff" * 32),  # wrong
    reward_chain_hash=real_rcb.get_hash(),
    num_blocks_overflow=uint8(0),
    new_difficulty=None,
    new_sub_slot_iters=None,
)

malicious_peak = NewPeakTimelord(
    reward_chain_block=real_rcb,
    difficulty=canonical_difficulty,
    deficit=uint8(0),                          # triggers SES inclusion path
    sub_slot_iters=canonical_ssi,
    sub_epoch_summary=fake_ses,                # injected fake SES
    previous_reward_challenges=[...],
    last_challenge_sb_or_eos_total_iters=uint128(...),
    passes_ses_height_but_not_yet_included=True,  # triggers get_next_sub_epoch_summary
)

# Send to timelord — accepted because real_rcb.weight > timelord's current weight
await timelord_api.new_peak_timelord(malicious_peak)

# After VDFs complete, _check_for_end_of_subslot builds EOS bundle with
# ses_hash = fake_ses.get_hash() != canonical_ses.get_hash()
# Full nodes call validate_unfinished_header_block → INVALID_SUB_EPOCH_SUMMARY
# Timelord is forked off canonical chain.
```

### Citations

**File:** chia/timelord/timelord_api.py (L59-110)
```python
    @metadata.request()
    async def new_peak_timelord(self, new_peak: NewPeakTimelord) -> None:
        if self.timelord.last_state is None:
            return None
        async with self.timelord.lock:
            if self.timelord.bluebox_mode:
                return None
            self.timelord.max_allowed_inactivity_time = 60

            if self.timelord.last_state.peak is None:
                # no known peak
                log.info("no last known peak, switching to new peak")
                self.timelord.new_peak = new_peak
                self.timelord.state_changed("new_peak", {"height": new_peak.reward_chain_block.height})
                return

            # new peak has equal weight but lower iterations
            if (
                self.timelord.last_state.get_weight() == new_peak.reward_chain_block.weight
                and self.timelord.last_state.peak.reward_chain_block.total_iters
                > new_peak.reward_chain_block.total_iters
            ):
                log.info(
                    "Not skipping peak, has equal weight but lower iterations,"
                    f"current peak:{self.timelord.last_state.total_iters} new peak "
                    f"{new_peak.reward_chain_block.total_iters}"
                    f"current rh: {self.timelord.last_state.peak.reward_chain_block.get_hash()}"
                    f"new peak rh: {new_peak.reward_chain_block.get_hash()}"
                )
                self.timelord.new_peak = new_peak
                self.timelord.state_changed("new_peak", {"height": new_peak.reward_chain_block.height})
                return

            # new peak is heavier
            if self.timelord.last_state.get_weight() < new_peak.reward_chain_block.weight:
                # if there is an unfinished block with less iterations, skip so we dont orphan it
                if (
                    new_peak.reward_chain_block.height == self.timelord.last_state.last_height + 1
                    and self.check_orphaned_unfinished_block(new_peak) is True
                ):
                    log.info("there is an unfinished block that this peak would orphan - skip peak")
                    self.timelord.state_changed("skipping_peak", {"height": new_peak.reward_chain_block.height})
                    return

                log.info(
                    "Not skipping peak, don't have. Maybe we are not the fastest timelord "
                    f"height: {new_peak.reward_chain_block.height} weight:"
                    f"{new_peak.reward_chain_block.weight} rh {new_peak.reward_chain_block.get_hash()}"
                )
                self.timelord.new_peak = new_peak
                self.timelord.state_changed("new_peak", {"height": new_peak.reward_chain_block.height})
                return
```

**File:** chia/timelord/timelord_state.py (L57-58)
```python
            self.deficit = state.deficit
            self.sub_epoch_summary = state.sub_epoch_summary
```

**File:** chia/timelord/timelord_state.py (L165-173)
```python
    def get_next_sub_epoch_summary(self) -> SubEpochSummary | None:
        if self.state_type in {StateType.FIRST_SUB_SLOT, StateType.END_OF_SUB_SLOT}:
            # Can only infuse SES after a peak (in an end of sub slot)
            return None
        assert self.peak is not None
        if self.passed_ses_height_but_not_yet_included and self.get_deficit() == 0:
            # This will mean we will include the ses in the next sub-slot
            return self.sub_epoch_summary
        return None
```

**File:** chia/timelord/timelord.py (L212-213)
```python
    async def on_connect(self, connection: WSChiaConnection) -> None:
        pass
```

**File:** chia/timelord/timelord.py (L837-865)
```python
            next_ses: SubEpochSummary | None = self.last_state.get_next_sub_epoch_summary()
            ses_hash: bytes32 | None
            if next_ses is not None:
                log.info(f"Including sub epoch summary{next_ses}")
                ses_hash = next_ses.get_hash()
                new_sub_slot_iters = next_ses.new_sub_slot_iters
                new_difficulty = next_ses.new_difficulty
            else:
                ses_hash = None
                new_sub_slot_iters = None
                new_difficulty = None
            cc_sub_slot = ChallengeChainSubSlot(cc_vdf, icc_sub_slot_hash, ses_hash, new_sub_slot_iters, new_difficulty)
            eos_deficit: uint8 = (
                self.last_state.get_deficit()
                if self.constants.MIN_BLOCKS_PER_CHALLENGE_BLOCK > self.last_state.get_deficit() > 0
                else self.constants.MIN_BLOCKS_PER_CHALLENGE_BLOCK
            )
            rc_sub_slot = RewardChainSubSlot(
                rc_vdf,
                cc_sub_slot.get_hash(),
                icc_sub_slot.get_hash() if icc_sub_slot is not None else None,
                eos_deficit,
            )
            eos_bundle = EndOfSubSlotBundle(
                cc_sub_slot,
                icc_sub_slot,
                rc_sub_slot,
                SubSlotProofs(cc_proof, icc_ip_proof, rc_proof),
            )
```

**File:** chia/timelord/timelord.py (L866-871)
```python
            if self._server is not None:
                msg = make_msg(
                    ProtocolMessageTypes.new_end_of_sub_slot_vdf,
                    timelord_protocol.NewEndOfSubSlotVDF(eos_bundle),
                )
                await self.server.send_to_all([msg], NodeType.FULL_NODE)
```

**File:** chia/consensus/block_header_validation.py (L436-456)
```python
                if check_sub_epoch_summary:
                    expected_sub_epoch_summary = make_sub_epoch_summary(
                        constants,
                        blocks,
                        height,
                        blocks.block_record(prev_b.prev_hash),
                        expected_vs.difficulty if can_finish_epoch else None,
                        expected_vs.ssi if can_finish_epoch else None,
                        make_challenge_root=pre_sp_tx_height >= constants.HARD_FORK2_HEIGHT,
                        prev_ses_block=expected_vs.prev_ses_block,
                    )
                    expected_hash = expected_sub_epoch_summary.get_hash()
                    if expected_hash != ses_hash:
                        log.error(f"{expected_sub_epoch_summary}")
                        return (
                            None,
                            ValidationError(
                                Err.INVALID_SUB_EPOCH_SUMMARY,
                                f"expected ses hash: {expected_hash} got {ses_hash} ",
                            ),
                        )
```

**File:** chia/protocols/timelord_protocol.py (L20-28)
```python
class NewPeakTimelord(Streamable):
    reward_chain_block: RewardChainBlock
    difficulty: uint64
    deficit: uint8
    sub_slot_iters: uint64  # SSi in the slot where NewPeak has been infused
    sub_epoch_summary: SubEpochSummary | None  # If NewPeak is the last slot in epoch, the next slot should include this
    previous_reward_challenges: list[tuple[bytes32, uint128]]
    last_challenge_sb_or_eos_total_iters: uint128
    passes_ses_height_but_not_yet_included: bool
```

**File:** chia/full_node/full_node.py (L899-936)
```python
            ses: SubEpochSummary | None = next_sub_epoch_summary(
                self.constants,
                self.blockchain,
                peak.required_iters,
                peak_block,
                True,
                post_hard_fork,
            )
            recent_rc = self.blockchain.get_recent_reward_challenges()

            curr = peak
            while not curr.is_challenge_block(self.constants) and not curr.first_in_sub_slot:
                curr = self.blockchain.block_record(curr.prev_hash)

            if curr.is_challenge_block(self.constants):
                last_csb_or_eos = curr.total_iters
            else:
                last_csb_or_eos = curr.ip_sub_slot_total_iters(self.constants)

            curr = peak
            passed_ses_height_but_not_yet_included = True
            while (curr.height % self.constants.SUB_EPOCH_BLOCKS) != 0:
                if curr.sub_epoch_summary_included:
                    passed_ses_height_but_not_yet_included = False
                curr = self.blockchain.block_record(curr.prev_hash)
            if curr.sub_epoch_summary_included or curr.height == 0:
                passed_ses_height_but_not_yet_included = False

            timelord_new_peak: timelord_protocol.NewPeakTimelord = timelord_protocol.NewPeakTimelord(
                peak_block.reward_chain_block,
                difficulty,
                peak.deficit,
                peak.sub_slot_iters,
                ses,
                recent_rc,
                last_csb_or_eos,
                passed_ses_height_but_not_yet_included,
            )
```
