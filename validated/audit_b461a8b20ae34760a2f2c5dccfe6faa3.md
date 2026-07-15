### Title
Malicious Full Node Peer Can Force FAILED Status on a Settled PENDING_CONFIRM Trade via Incomplete `get_coin_state` Response — (`chia/wallet/trade_manager.py`)

### Summary

`coins_of_interest_farmed()` unconditionally trusts a single peer's response to a follow-up `get_coin_state()` call to decide whether a trade succeeded or failed. A malicious full node peer can send a valid-looking `CoinState` update for a coin of interest and then return an empty list for the settlement coin query, causing the wallet to permanently mark a successfully settled PENDING_CONFIRM trade as FAILED and delete its transaction records.

### Finding Description

In `coins_of_interest_farmed()`, after receiving a `CoinState` update for a coin of interest, the wallet queries the **same peer** for the states of its own settlement payment coins (`our_addition_ids`):

```python
coin_states = await self.wallet_state_manager.wallet_node.get_coin_state(
    our_addition_ids,
    peer=peer,          # ← same untrusted peer
    fork_height=fork_height,
)
assert coin_states is not None
coin_state_names: list[bytes32] = [cs.coin.name() for cs in coin_states]
if set(our_addition_ids) == set(coin_state_names):
    # CONFIRMED path
    ...
else:
    # In any other scenario this trade failed
    await self.wallet_state_manager.delete_trade_transactions(trade.trade_id)
    if trade.status == TradeStatus.PENDING_CANCEL.value:
        await self.trade_store.set_status(trade.trade_id, TradeStatus.CANCELLED)
    elif trade.status == TradeStatus.PENDING_CONFIRM.value:
        await self.trade_store.set_status(trade.trade_id, TradeStatus.FAILED)
``` [1](#0-0) 

If the peer returns `[]` (an empty but non-`None` list), the `assert coin_states is not None` passes, `coin_state_names` is empty, `set(our_addition_ids) != set([])` (when `our_addition_ids` is non-empty), and the `else` branch executes unconditionally — deleting all trade transaction records and setting status to `FAILED`.

There is no cross-verification against a trusted/local source, no quorum check across multiple peers, and no guard that prevents the `FAILED` transition when the peer's response is merely incomplete rather than authoritative.

### Impact Explanation

- The trade's `OUTGOING_TRADE` and `INCOMING_TRADE` `TransactionRecord`s are permanently deleted via `delete_trade_transactions`.
- The `TradeRecord` is permanently set to `TradeStatus.FAILED`, even though the offer was settled on-chain.
- The wallet's trade history is corrupted and cannot be recovered from the local DB.
- While the actual received coins may eventually be re-discovered through hint/puzzle-hash subscriptions, the accounting records (trade linkage, sent amounts, received amounts) are gone, and the trade UI permanently shows FAILED for a completed trade.
- This falls squarely under **High: Corruption of offer/trade settlement state with direct security impact**.

### Likelihood Explanation

Any peer the wallet connects to can execute this attack. The wallet's peer selection is not restricted to trusted nodes by default. The attacker only needs to:
1. Operate a full node that the victim wallet connects to.
2. Know which coins the wallet is watching (trivially available because the wallet sends `RegisterForCoinUpdates` subscriptions to the peer).
3. Send a crafted `CoinState` with `spent_height` set for any watched coin, then return `[]` for the follow-up `get_coin_state` call.

No key material, admin access, or cryptographic break is required.

### Recommendation

- Do **not** use the same untrusted peer for the follow-up `get_coin_state` call that determines trade success/failure. Query a trusted/local source (e.g., the local full node's coin store, or a quorum of peers).
- Alternatively, require that `get_coin_state` returns a non-empty result before entering the `else` (failure) branch; treat an empty/incomplete response as an inconclusive result and defer the status transition.
- Add an explicit guard: only transition to `FAILED` if the peer's response is verified to be complete (e.g., by cross-checking against the local coin DB or requiring the response to include at least the coins that were confirmed spent in the triggering `CoinState`).

### Proof of Concept

```python
# Pseudocode test plan
async def test_malicious_peer_forces_failed_status():
    # 1. Set up a taker wallet with a PENDING_CONFIRM trade
    #    (our_addition_ids is non-empty — taker offered XCH/CAT)
    trade = await setup_pending_confirm_trade(taker_wallet)
    assert trade.status == TradeStatus.PENDING_CONFIRM.value

    # 2. Mock get_coin_state to return [] (malicious peer omits settlement coins)
    malicious_peer = MockPeer()
    malicious_peer.get_coin_state = AsyncMock(return_value=[])

    # 3. Send a CoinState for a coin of interest with spent_height set
    coin_of_interest = trade.coins_of_interest[0]
    fake_coin_state = CoinState(coin=coin_of_interest, spent_height=uint32(1000), created_height=uint32(999))

    # 4. Trigger coins_of_interest_farmed
    await trade_manager.coins_of_interest_farmed(fake_coin_state, fork_height=None, peer=malicious_peer)

    # 5. Assert: trade must NOT be FAILED — but it will be, demonstrating the bug
    refreshed = await trade_manager.get_trade_by_id(trade.trade_id)
    assert refreshed.status != TradeStatus.FAILED.value  # This assertion FAILS
    # Transaction records are also deleted — balance is corrupted
```

The `assert coin_states is not None` on line 184 passes for `[]`, the set comparison on line 187 fails, and the `FAILED` path on line 206 executes — permanently corrupting the trade state based solely on one peer's incomplete response. [2](#0-1)

### Citations

**File:** chia/wallet/trade_manager.py (L179-207)
```python
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
