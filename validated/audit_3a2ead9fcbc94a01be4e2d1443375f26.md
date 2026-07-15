### Title
Trade Settlement State Corruption via Peer-Controlled `get_coin_state` Response in `coins_of_interest_farmed` - (File: chia/wallet/trade_manager.py)

---

### Summary

`TradeManager.coins_of_interest_farmed` makes an external network call to `get_coin_state` on the triggering peer **before** writing the trade's final status. Because the peer is free to omit coin states from its response without being banned, a malicious untrusted peer can cause a wallet to permanently mark a blockchain-confirmed trade as `FAILED` and delete its transaction records, corrupting offer/trade settlement state.

---

### Finding Description

`coins_of_interest_farmed` is the function that decides whether a pending trade was successfully settled or failed. It is invoked from `WalletStateManager._add_coin_states` inside an open `db_wrapper.writer()` transaction whenever a coin belonging to a tracked trade is observed as spent.

The function's logic is:

1. **Read** the current trade record from the DB (line 151).
2. Compute `our_addition_ids` — the settlement-payment coin IDs that should exist on-chain if the trade succeeded (lines 167–176).
3. **External call** — ask the same peer for the coin states of those settlement coins (lines 179–183).
4. **Write** trade status based on the peer's answer (lines 187–206):
   - If the peer returns all `our_addition_ids` → `TradeStatus.CONFIRMED` (line 190).
   - Otherwise → `delete_trade_transactions` + `TradeStatus.FAILED` or `CANCELLED` (lines 201–206). [1](#0-0) 

The external call at step 3 goes through `WalletNode.get_coin_state`: [2](#0-1) 

For untrusted peers, the method validates each **returned** coin state against the blockchain (lines 1810–1814). However, it only bans a peer for returning an **unrequested** coin (line 1806–1809). There is **no check** that the peer returned all requested coin states. A peer may silently omit any subset of the requested states and the wallet will accept the truncated list as the complete answer.

The call site in `_add_coin_states` that triggers this path: [3](#0-2) 

The outer DB writer context that is held open across the external call: [4](#0-3) 

---

### Impact Explanation

When the peer omits one or more settlement-payment coin states:

- `set(our_addition_ids) != set(coin_state_names)` evaluates to `True`.
- `delete_trade_transactions(trade.trade_id)` is called — permanently removing the transaction records for a trade that was actually confirmed on-chain.
- The trade is written to the DB as `TradeStatus.FAILED` (PENDING_CONFIRM path) or `TradeStatus.CANCELLED` (PENDING_CANCEL path).

Once a trade is `FAILED`, it is no longer included in `get_coins_of_interest` (which only watches `PENDING_ACCEPT`, `PENDING_CONFIRM`, `PENDING_CANCEL`), so the wallet will never re-evaluate it. The wallet permanently believes the trade failed. The user loses the trade history record and may take incorrect follow-up actions (e.g., re-submitting an already-settled offer, double-spending, or failing to account for received assets in downstream logic).

This matches: **High — Corruption of offer/trade settlement state with direct security impact.**

---

### Likelihood Explanation

An unprivileged attacker only needs to operate a reachable Chia full node. Wallets in light-client mode connect to full nodes they discover via DNS introducers or peer exchange; the attacker does not need any keys, admin access, or cryptographic break. The attack window is every time a trade coin is spent on-chain — a normal, frequent event for any active trading wallet.

---

### Recommendation

1. **Completeness check**: After `get_coin_state` returns, verify that `set(coin_state_names) == set(our_addition_ids)` **and** that the peer returned exactly as many states as were requested. If the count is short, treat the response as inconclusive rather than as evidence of failure, and retry against a different peer or defer the decision.

2. **Separate the external call from the state write**: Resolve the coin states before entering the `db_wrapper.writer()` block, or at minimum do not write a terminal status (`FAILED`/`CANCELLED`) based solely on a single peer's truncated response.

3. **Require a quorum or a trusted source**: For terminal trade-status decisions, cross-check against a second peer or require the response to come from a trusted full node.

---

### Proof of Concept

1. Attacker runs a patched Chia full node that responds normally to all protocol messages **except** `RegisterForCoinUpdates`: when the wallet queries for settlement-payment coin IDs, the node returns an empty `RespondToCoinUpdates` (zero coin states). This is protocol-legal and does not trigger the ban at line 1806.
2. Victim wallet connects to the attacker's node as an untrusted peer.
3. Victim has a trade in `PENDING_CONFIRM` state; the counterparty settles it on-chain.
4. The attacker's node sends the victim a valid `CoinStateUpdate` for one of the trade's input coins (the coin is genuinely spent, so the state passes `validate_received_state_from_peer`).
5. `_add_coin_states` detects `coin_name in trade_removals` and calls `coins_of_interest_farmed`.
6. `coins_of_interest_farmed` calls `get_coin_state(our_addition_ids, peer=attacker_node)`.
7. Attacker node returns `[]`. Validation loop in `get_coin_state` iterates over zero items; `valid_list = []` is returned.
8. `set(our_addition_ids) != set([])` → else branch: `delete_trade_transactions` runs, trade is written as `TradeStatus.FAILED`.
9. Victim's wallet permanently shows the trade as failed; the trade's transaction records are gone. The settlement-payment coins will eventually appear in the coin store via a separate sync path, but the trade record linking them to the offer is destroyed. [5](#0-4) [6](#0-5)

### Citations

**File:** chia/wallet/trade_manager.py (L178-207)
```python
            # And get all relevant coin states
            coin_states = await self.wallet_state_manager.wallet_node.get_coin_state(
                our_addition_ids,
                peer=peer,
                fork_height=fork_height,
            )
            assert coin_states is not None
            coin_state_names: list[bytes32] = [cs.coin.name() for cs in coin_states]
            # If any of our settlement_payments were spent, this offer was a success!
            if set(our_addition_ids) == set(coin_state_names):
                height = coin_state.spent_height
                assert height is not None
                await self.trade_store.set_status(trade.trade_id, TradeStatus.CONFIRMED, index=height)
                tx_records: list[TransactionRecord] = await self.calculate_tx_records_for_offer(offer, False)
                for tx in tx_records:
                    if TradeStatus(trade.status) == TradeStatus.PENDING_ACCEPT:
                        await self.wallet_state_manager.add_transaction(
                            dataclasses.replace(tx, confirmed_at_height=height, confirmed=True)
                        )

                self.log.info(f"Trade with id: {trade.trade_id} confirmed at height: {height}")
            else:
                # In any other scenario this trade failed
                await self.wallet_state_manager.delete_trade_transactions(trade.trade_id)
                if trade.status == TradeStatus.PENDING_CANCEL.value:
                    await self.trade_store.set_status(trade.trade_id, TradeStatus.CANCELLED)
                    self.log.info(f"Trade with id: {trade.trade_id} canceled")
                elif trade.status == TradeStatus.PENDING_CONFIRM.value:
                    await self.trade_store.set_status(trade.trade_id, TradeStatus.FAILED)
                    self.log.warning(f"Trade with id: {trade.trade_id} failed")
```

**File:** chia/wallet/wallet_node.py (L1795-1817)
```python
    async def get_coin_state(
        self, coin_names: list[bytes32], peer: WSChiaConnection, fork_height: uint32 | None = None
    ) -> list[CoinState]:
        msg = RegisterForCoinUpdates(coin_names, uint32(0))
        coin_state: RespondToCoinUpdates | None = await peer.call_api(FullNodeAPI.register_for_coin_updates, msg)
        if coin_state is None or not isinstance(coin_state, RespondToCoinUpdates):
            raise PeerRequestException(f"Was not able to get states for {coin_names}")

        if not self.is_trusted(peer):
            valid_list = []
            for coin in coin_state.coin_states:
                if coin.coin.name() not in coin_names:
                    await peer.close(9999)
                    self.log.warning(f"Peer {peer.peer_node_id} sent us an unrequested coin state. Banning.")
                    raise PeerRequestException(f"Peer sent us unrequested coin state {coin}")
                valid = await self.validate_received_state_from_peer(
                    coin, peer, self.get_cache_for_peer(peer), fork_height
                )
                if valid:
                    valid_list.append(coin)
            return valid_list

        return coin_state.coin_states
```

**File:** chia/wallet/wallet_state_manager.py (L1792-1792)
```python
                async with self.db_wrapper.writer():
```

**File:** chia/wallet/wallet_state_manager.py (L1817-1818)
```python
                    if coin_state.spent_height is not None and coin_name in trade_removals:
                        await self.trade_manager.coins_of_interest_farmed(coin_state, fork_height, peer)
```
