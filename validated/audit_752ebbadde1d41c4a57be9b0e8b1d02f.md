Based on my thorough code analysis, this is a real vulnerability. Let me trace the exact path.

---

### Title
Untrusted Peer Triggers Unconditional `perform_atomic_rollback(0)` via Short-Chain Backtrack — (`chia/wallet/wallet_node.py`)

### Summary

An unprivileged remote peer can cause the wallet to erase all confirmed coin records and transaction history by sending a fake `NewPeakWallet` message with height `H < WEIGHT_PROOF_RECENT_BLOCKS`. The cap guard inside `wallet_short_sync_backtrack` is gated on `header_block.height >= WEIGHT_PROOF_RECENT_BLOCKS`, so it never fires for short chains. The peer then serves a consistent but disconnected hash chain down to height 0, causing `perform_atomic_rollback(0)` to execute **before** any block validation occurs.

### Finding Description

**Entry point — `new_peak_wallet`** (line 1191):

The wallet accepts `NewPeakWallet` from any connected peer. The only pre-filter is a weight comparison against the current peak (line 1202) and a timestamp plausibility check (line 1232). Both are trivially satisfied by the attacker, who controls all fields in the message and the `RespondBlockHeader` response. [1](#0-0) [2](#0-1) 

**Routing — `new_peak_from_untrusted`** (line 1273):

When `new_peak_hb.height < constants.WEIGHT_PROOF_RECENT_BLOCKS`, the code takes the short-chain branch at line 1279 and calls `sync_from_untrusted_close_to_peak` **without requesting or validating a weight proof**. This is the critical bypass. [3](#0-2) 

**Cap guard is dead for short chains — `wallet_short_sync_backtrack`** (line 1393):

The loop cap that would abort and return `None` requires all three conditions:
- `peak_hb is not None` ✓ (wallet has existing state)
- `len(blocks) > LONG_SYNC_THRESHOLD` ✓ (after enough iterations)
- **`header_block.height >= constants.WEIGHT_PROOF_RECENT_BLOCKS`** ✗ — always False when H < WEIGHT_PROOF_RECENT_BLOCKS

The third condition is never satisfied, so the cap never fires and the loop runs all the way to height 0. [4](#0-3) 

**Discontinuity check is bypassable:**

The check at line 1425 verifies `prev_head.header_hash == top.prev_header_hash`. The attacker pre-computes a chain of fake `HeaderBlock` objects where each block's `header_hash` (a deterministic hash of its content) equals the next block's `prev_header_hash`. This is trivially constructable — no proof of space or VDF is required at this stage. [5](#0-4) 

**Rollback fires before validation:**

Once the loop exits at `top.height == 0`, `should_skip_rollback` is set to `peak_hb is None`. Since `peak_hb is not None` (the wallet has existing state), `should_skip_rollback = False`. The rollback at line 1447 is then executed **before** the `add_block` loop at line 1450. [6](#0-5) 

Even if the subsequent `add_block` calls fail with `INVALID_BLOCK` (because the fake blocks have no valid PoSpace/VDF), the rollback is already committed to the database. [7](#0-6) 

`perform_atomic_rollback` calls `reorg_rollback(0)` inside a DB writer transaction, permanently erasing all coin records and transaction history. [8](#0-7) 

### Impact Explanation

Complete erasure of all wallet-side coin records (XCH, CATs, NFTs, DIDs, VCs, pool wallet coins) and transaction history. The wallet reports zero balance and cannot sign or submit transactions until a full re-sync completes. This matches the **High** impact category: corruption of coin records and wallet sync state with direct security impact.

### Likelihood Explanation

Any peer the wallet connects to (the wallet connects to untrusted peers whenever no trusted local node is configured, which is the default for most users) can trigger this with a single crafted message sequence. No keys, admin access, or broken cryptography are required. The attacker only needs to pre-compute a hash chain of `H` fake header blocks.

### Recommendation

Move the rollback to **after** successful block validation, or require that `perform_atomic_rollback` is only called when the new chain has been validated (either via weight proof or by successfully adding all blocks). Alternatively, extend the cap guard to also fire when `peak_hb is not None` regardless of `WEIGHT_PROOF_RECENT_BLOCKS`, requiring a weight proof for any backtrack that reaches genesis with existing wallet state.

### Proof of Concept

```python
# Attacker pre-computes a chain of H fake HeaderBlocks (H < WEIGHT_PROOF_RECENT_BLOCKS)
# where each block's header_hash == next block's prev_header_hash.
# Attacker sends NewPeakWallet(height=H, weight=current_peak_weight+1, header_hash=chain_tip_hash)
# Wallet fetches header block -> attacker returns chain_tip (consistent hash/weight/height)
# Timestamp: attacker sets foliage_transaction_block.timestamp = int(time.time())
# new_peak_from_untrusted -> height < WEIGHT_PROOF_RECENT_BLOCKS -> sync_from_untrusted_close_to_peak
# wallet_short_sync_backtrack: cap guard never fires (height < WEIGHT_PROOF_RECENT_BLOCKS)
# Attacker serves chain[H-1], chain[H-2], ..., chain[0] — each hash-consistent
# Loop exits at top.height == 0
# should_skip_rollback = False (peak_hb is not None)
# perform_atomic_rollback(0) called -> all coin records deleted
# add_block calls fail with INVALID_BLOCK (no valid PoSpace) but rollback already committed
``` [9](#0-8)

### Citations

**File:** chia/wallet/wallet_node.py (L1202-1205)
```python
        if peak_hb is not None and new_peak.weight < peak_hb.weight:
            # Discards old blocks,  accept only heavier peaks blocks that are equal in weight to peak
            self.log.debug("skip block with lower weight.")
            return
```

**File:** chia/wallet/wallet_node.py (L1231-1238)
```python
        latest_timestamp = await self.get_timestamp_for_height_from_peer(new_peak_hb.height, peer)
        if latest_timestamp is None or not self.is_timestamp_in_sync(latest_timestamp):
            if trusted:
                self.log.debug(f"Trusted peer {peer.get_peer_info()} is not synced.")
            else:
                self.log.warning(f"Non-trusted peer {peer.get_peer_info()} is not synced, disconnecting")
                await peer.close(120)
            return
```

**File:** chia/wallet/wallet_node.py (L1279-1281)
```python
        if new_peak_hb.height < self.constants.WEIGHT_PROOF_RECENT_BLOCKS:
            # this is the case happens chain is shorter then WEIGHT_PROOF_RECENT_BLOCKS
            return await self.sync_from_untrusted_close_to_peak(new_peak_hb, peer)
```

**File:** chia/wallet/wallet_node.py (L1393-1456)
```python
    async def wallet_short_sync_backtrack(
        self, peak_hb: HeaderBlock | None, header_block: HeaderBlock, peer: WSChiaConnection
    ) -> int | None:
        top = header_block
        blocks = [top]
        # Fetch blocks backwards until we hit the one that we have,
        # then complete them with additions / removals going forward
        fork_height = 0
        should_skip_rollback = False
        if self.wallet_state_manager.blockchain.contains_block(header_block.prev_header_hash):
            fork_height = header_block.height - 1

        while not self.wallet_state_manager.blockchain.contains_block(top.prev_header_hash) and top.height > 0:
            if self._shut_down:
                raise RuntimeError("Shutdown requested during wallet backtrack sync")
            if (
                peak_hb is not None
                and len(blocks) > self.LONG_SYNC_THRESHOLD
                and header_block.height >= self.constants.WEIGHT_PROOF_RECENT_BLOCKS
            ):
                self.log.info(
                    f"Backtrack exceeded {self.LONG_SYNC_THRESHOLD} headers at height "
                    f"{header_block.height}, switching to long sync for peer {peer.peer_info.host}"
                )
                return None
            request_prev = RequestBlockHeader(uint32(top.height - 1))
            response_prev: RespondBlockHeader | None = await peer.call_api(
                FullNodeAPI.request_block_header, request_prev
            )
            if response_prev is None or not isinstance(response_prev, RespondBlockHeader):
                raise RuntimeError("bad block header response from peer while syncing")
            prev_head = response_prev.header_block
            if prev_head.header_hash != top.prev_header_hash:
                self.log.warning(
                    f"Backtrack chain discontinuity at height {prev_head.height}, "
                    f"disconnecting peer {peer.peer_info.host}"
                )
                await peer.close()
                return None
            blocks.append(prev_head)
            top = prev_head
            fork_height = top.height - 1

        blocks.reverse()

        if top.height == 0:
            fork_height = 0
            should_skip_rollback = peak_hb is None

        # Roll back coins and transactions
        peak_height = await self.wallet_state_manager.blockchain.get_finished_sync_up_to()
        if not should_skip_rollback and fork_height < peak_height:
            self.log.info(f"Rolling back to {fork_height}")
            # we should clear all peers since this is a full rollback
            await self.perform_atomic_rollback(fork_height)
            await self.update_ui()

        for block in blocks:
            # Set blockchain to the latest peak
            res, err = await self.wallet_state_manager.blockchain.add_block(block)
            if res == AddBlockResult.INVALID_BLOCK:
                raise ValueError(err)

        return fork_height
```

**File:** chia/wallet/wallet_node.py (L1826-1832)
```python
        if not self.is_trusted(peer):
            request_cache = self.get_cache_for_peer(peer)
            validated = []
            for state in response.coin_states:
                valid = await self.validate_received_state_from_peer(state, peer, request_cache, fork_height)
                if valid:
                    validated.append(state)
```
