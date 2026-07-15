### Title
`validate_removals` bulk-mode (`proofs=None`) does not verify the queried coin is present — wallet accepts fabricated spend claim — (`chia/wallet/util/wallet_sync_utils.py`)

---

### Summary

When `validate_removals` is called with `proofs=None` (bulk mode), it only verifies that the Merkle root of the returned non-`None` coins matches the block's `removals_root`. It does **not** verify that the specific coin being queried is actually present in the returned list. A malicious peer can exploit this by responding with `proofs=None` (even though the wallet always requests specific coin names) and supplying a valid set of real removals that excludes the victim coin. `validate_removals` returns `True`, and the wallet marks the victim coin as spent when it was not.

---

### Finding Description

**Entrypoint:** `request_and_validate_removals` in `chia/wallet/util/wallet_sync_utils.py`

The wallet always issues a targeted request:

```python
removals_request = RequestRemovals(height, header_hash, [coin_name])
``` [1](#0-0) 

The response is passed directly to `validate_removals`:

```python
return validate_removals(removals_res.coins, removals_res.proofs, removals_root)
``` [2](#0-1) 

There is **no enforcement** that the peer must respond with `proofs != None` when the request contained specific coin names. A malicious peer can freely respond with `proofs=None`.

In `validate_removals`, the `proofs is None` branch:

```python
removals_items = [name for name, coin in coins if coin is not None]
removals_root = bytes32(compute_merkle_set_root(removals_items))
if root != removals_root:
    return False
``` [3](#0-2) 

This only checks that the Merkle root of the returned non-`None` coins matches the block's `removals_root`. It does **not** check whether the specific `coin_name` that was queried is actually present in `coins`. The function returns `True` as long as the root matches, regardless of whether the queried coin appears in the list.

After `validate_removals` returns `True`, `validate_received_state_from_peer` proceeds unconditionally:

```python
validate_removals_result = await request_and_validate_removals(
    peer, spent_state_block.height, spent_state_block.header_hash,
    coin_state.coin.name(),
    spent_state_block.foliage_transaction_block.removals_root,
)
if validate_removals_result is None:
    return False
if validate_removals_result is False:
    ...
    return False
# Falls through — coin_state is trusted
peer_request_cache.add_to_states_validated(coin_state)
return True
``` [4](#0-3) 

The wallet then processes the peer-supplied `CoinState(victim_coin, spent_height, created_height)` as valid, marking the coin as spent in `_add_coin_states`. [5](#0-4) 

---

### Impact Explanation

A malicious peer can cause the wallet to mark any real, unspent coin belonging to the victim as spent. The wallet's confirmed balance is incorrectly reduced. The victim coin record is written to the local DB as spent at a height where it was not actually removed. This is a **High** impact: corruption of wallet sync state (coin records, balance) with direct security impact, reachable by any unprivileged peer acting as a full node.

---

### Likelihood Explanation

Any peer the wallet connects to (untrusted full node) can execute this attack. The attacker needs only:
1. Knowledge of a real coin belonging to the victim (public blockchain data).
2. Knowledge of the real removals at any transaction block height (also public).
3. The ability to serve a crafted `RespondRemovals` with `proofs=None`.

No key material, admin access, or cryptographic break is required.

---

### Recommendation

In `request_and_validate_removals`, after calling `validate_removals`, explicitly verify that the queried `coin_name` appears in `removals_res.coins`. Alternatively, in `validate_removals`, when `proofs is None`, reject the response if the caller supplied specific coin names (i.e., enforce that `proofs=None` responses are only accepted when `coin_names=None` was sent). The simplest fix is in `request_and_validate_removals`:

```python
result = validate_removals(removals_res.coins, removals_res.proofs, removals_root)
if result and removals_res.proofs is None:
    # Bulk mode: verify the queried coin is actually in the response
    coins_dict = dict(removals_res.coins)
    if coin_name not in coins_dict or coins_dict[coin_name] is None:
        return False
return result
```

---

### Proof of Concept

1. Wallet connects to a malicious peer (untrusted full node).
2. Malicious peer sends `CoinStateUpdate` containing `CoinState(victim_coin, spent_height=H, created_height=C)` where `victim_coin` is a real, unspent coin belonging to the victim wallet.
3. Wallet calls `request_and_validate_removals(peer, H, header_hash_H, victim_coin.name(), removals_root_H)`.
4. Malicious peer responds with:
   ```
   RespondRemovals(
       height=H,
       header_hash=header_hash_H,
       coins=[(real_removed_coin_name, real_removed_coin)],  # victim_coin NOT included
       proofs=None  # bulk mode, even though wallet requested specific coin
   )
   ```
   where `real_removed_coin_name` is the name of a coin actually removed at height `H`, so `compute_merkle_set_root([real_removed_coin_name]) == removals_root_H`.
5. `validate_removals` computes the Merkle root of `[real_removed_coin_name]`, finds it matches `removals_root_H`, and returns `True`. [6](#0-5) 
6. `validate_received_state_from_peer` returns `True`; the wallet processes the `CoinState` and marks `victim_coin` as spent at height `H`.
7. Victim wallet balance is incorrectly reduced by the value of `victim_coin`.

### Citations

**File:** chia/wallet/util/wallet_sync_utils.py (L133-141)
```python
    if proofs is None:
        # If there are no proofs, it means all removals were returned in the response.
        # we must find the ones relevant to our wallets.

        # Verify removals root
        removals_items = [name for name, coin in coins if coin is not None]
        removals_root = bytes32(compute_merkle_set_root(removals_items))
        if root != removals_root:
            return False
```

**File:** chia/wallet/util/wallet_sync_utils.py (L182-182)
```python
    removals_request = RequestRemovals(height, header_hash, [coin_name])
```

**File:** chia/wallet/util/wallet_sync_utils.py (L194-194)
```python
    return validate_removals(removals_res.coins, removals_res.proofs, removals_root)
```

**File:** chia/wallet/wallet_node.py (L1626-1642)
```python
            validate_removals_result = await request_and_validate_removals(
                peer,
                spent_state_block.height,
                spent_state_block.header_hash,
                coin_state.coin.name(),
                spent_state_block.foliage_transaction_block.removals_root,
            )
            if validate_removals_result is None:
                return False
            if validate_removals_result is False:
                self.log.warning("Validate false 3")
                await peer.close(9999)
                return False
            validated = await self.validate_block_inclusion(spent_state_block, peer, peer_request_cache)
            if not validated:
                return False
        peer_request_cache.add_to_states_validated(coin_state)
```

**File:** chia/wallet/wallet_state_manager.py (L1908-1910)
```python
                    # if the coin has been spent
                    elif coin_state.created_height is not None and coin_state.spent_height is not None:
                        self.log.debug("Coin spent: %s", coin_state)
```
