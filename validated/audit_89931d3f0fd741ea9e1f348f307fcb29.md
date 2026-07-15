### Title
REMOTE Coin Records Not Purged on Reorg — (`chia/wallet/wallet_state_manager.py`)

### Summary

The `_add_coin_states` handler in `WalletStateManager` contains an explicit `pass` (with TODO comments) for the `CoinState.created_height is None` case, which is the protocol signal for a reorg-removed coin. When a coin registered under a `WalletType.REMOTE` wallet is confirmed on-chain and then removed by a reorg, the wallet receives `CoinState(coin, created_height=None, spent_height=None)` from its peer but takes no action to delete or roll back the stored `WalletCoinRecord`. The phantom record persists indefinitely.

### Finding Description

In `_add_coin_states`, after the wallet resolves a `wallet_identifier` for the coin, execution reaches the three-way branch on `coin_state.created_height`: [1](#0-0) 

```python
if coin_state.created_height is None:
    # TODO implements this coin got reorged
    # TODO: we need to potentially roll back the pool wallet here
    pass
```

No deletion, no `set_spent`, no rollback — the function simply continues to the next coin state. The early-exit guard at lines 1806–1815 does **not** fire for this case because the local record's `confirmed_block_height` (a non-zero integer) will never equal `coin_state.created_height` (which is `None`), so the `continue` is skipped and execution falls through to the `pass`. [2](#0-1) 

For a REMOTE coin specifically, the wallet identifier is resolved from the local record at lines 1821–1827 (because `local_record.wallet_type == WalletType.REMOTE` and the remote wallet id is in `self.wallets`), so `wallet_identifier` is non-`None` and the code does not fall into the "no wallet" branch that skips processing. It reaches the `pass` with a valid `wallet_identifier` and a stale `local_record`, and does nothing. [3](#0-2) 

### Impact Explanation

After the reorg, `coin_store.get_coin_record(coin_id)` still returns the old `WalletCoinRecord` with `wallet_type == WalletType.REMOTE`. The REMOTE wallet's balance computation will count this phantom coin, reporting a balance that is higher than the canonical chain state. Wallet sync state is durably corrupted: the phantom record survives restarts because it is written to the SQLite coin store.

### Likelihood Explanation

Reorgs are a normal part of Chia chain operation. Any peer (including a legitimately-connected full node) that delivers a `CoinState(created_height=None, spent_height=None)` for a REMOTE-registered coin triggers the bug. No special attacker capability is required beyond being the wallet's connected peer during a reorg event. A malicious peer can also synthesize this message for any coin the wallet has registered as REMOTE.

### Recommendation

In the `created_height is None` branch, add logic to delete the coin record from the coin store (and notify the relevant wallet) when a local record exists. For REMOTE wallets specifically, `await self.coin_store.delete_coin_record(coin_name)` (or equivalent rollback) should be called. The pool wallet rollback noted in the second TODO should also be addressed.

### Proof of Concept

Integration test outline:
1. Create a coin sent to an external puzzle hash.
2. Create a `RemoteWallet` and call `register_remote_coins` with that coin's ID.
3. Process blocks so the coin is confirmed; assert `coin_store.get_coin_record(coin_id)` returns a record with `wallet_type == WalletType.REMOTE`.
4. Deliver `CoinState(coin=created_coin, created_height=None, spent_height=None)` via `_add_coin_states` (simulating a reorg from the peer).
5. Assert `coin_store.get_coin_record(coin_id)` returns `None`.

Step 5 will **fail** against the current code, confirming the phantom record persists. [1](#0-0) [4](#0-3)

### Citations

**File:** chia/wallet/wallet_state_manager.py (L1802-1815)
```python
                    if local_record is not None:
                        local_spent = None
                        if local_record.spent_block_height != 0:
                            local_spent = local_record.spent_block_height
                        if (
                            local_spent == coin_state.spent_height
                            and local_record.confirmed_block_height == coin_state.created_height
                            and not (
                                local_record.wallet_type == WalletType.REMOTE
                                and wallet_identifier is not None
                                and wallet_identifier.type != WalletType.REMOTE
                            )
                        ):
                            continue
```

**File:** chia/wallet/wallet_state_manager.py (L1821-1827)
```python
                    elif local_record is not None and (
                        local_record.wallet_type != WalletType.REMOTE or uint32(local_record.wallet_id) in self.wallets
                    ):
                        # If we already have a local coin record, use it as a fallback wallet identifier.
                        # This includes REMOTE records so a later spent update can flow through set_spent()
                        # rather than relying on add_coin_record() replacement semantics.
                        wallet_identifier = WalletIdentifier(uint32(local_record.wallet_id), local_record.wallet_type)
```

**File:** chia/wallet/wallet_state_manager.py (L1889-1892)
```python
                    if coin_state.created_height is None:
                        # TODO implements this coin got reorged
                        # TODO: we need to potentially roll back the pool wallet here
                        pass
```

**File:** chia/_tests/wallet/remote_wallet/test_remote_wallet.py (L88-91)
```python
    record = await wallet_node.wallet_state_manager.coin_store.get_coin_record(coin_id)
    assert record is not None
    assert record.wallet_type == WalletType.REMOTE
    assert record.wallet_id == int(remote_wallet.id())
```
