### Title
Missing `coin_store.set_spent` for Spent Pool Reward Coins in `PLOTNFT_2` Wallet Sync — (`File: chia/wallet/wallet_state_manager.py`)

### Summary

In `WalletStateManager._add_coin_states`, when a `PLOTNFT_2` pool reward coin is observed as spent on-chain, the code correctly updates `plotnft2_store` via `mark_pool_reward_as_spent`, but **never calls `coin_store.set_spent`**. The parallel branch for a spent PlotNFT singleton coin does call `coin_store.set_spent`. This asymmetry leaves pool reward coins permanently marked as unspent in the wallet coin store, inflating the wallet's reported spendable balance and corrupting wallet sync state.

### Finding Description

Inside `_add_coin_states`, the `WalletType.PLOTNFT_2` branch handles two sub-cases for a spent coin:

1. **PlotNFT singleton coin** (`isinstance(coin_data, PlotNFT)`): calls `coin_added`, then `coin_store.set_spent`, then `add_interested_coin_ids`.
2. **Pool reward coin** (`else`): calls only `plotnft2_store.mark_pool_reward_as_spent`.

The `else` branch is missing the call to `self.coin_store.set_spent(coin_name, uint32(coin_state.spent_height))`.

Pool reward coins are added to the wallet coin store when first received (via `coin_added` → `coin_store.add_coin_record` in `PlotNFT2Wallet.coin_added`). When the pool later spends them (via `forward_pool_reward`), the `plotnft2_store` is updated but the `WalletCoinStore` record is never marked spent. [1](#0-0) 

Compare the singleton branch which correctly calls `set_spent`: [2](#0-1) 

Pool reward coins are added to the coin store here: [3](#0-2) 

`get_confirmed_balance` and `get_unconfirmed_balance` both query `coin_store.get_unspent_coins_for_wallet`, so they will permanently include forwarded pool reward coins: [4](#0-3) 

`mark_pool_reward_as_spent` only updates the `plotnft2_store`; it does not touch the wallet coin store: [5](#0-4) 

### Impact Explanation

After a pool calls `forward_pool_reward` and the spend is confirmed on-chain, the wallet's `WalletCoinStore` still shows the pool reward coin as unspent. This causes:

1. **Inflated confirmed and spendable balance** — `get_confirmed_balance` and `get_unconfirmed_balance` both read from `coin_store.get_unspent_coins_for_wallet`, which returns the stale unspent record. The wallet permanently over-reports its balance by the sum of all forwarded pool reward amounts.
2. **Corrupted wallet sync state** — the coin record diverges from on-chain reality. On resync or reorg, the stale record can cause further inconsistencies.
3. **Potential failed transactions** — the wallet may attempt to include these phantom unspent coins as fee inputs in future transactions, producing spend bundles that are rejected by the mempool because the coins are already spent on-chain.

This matches the allowed High impact: *"Corruption of coin records … wallet sync state … with direct security impact."*

### Likelihood Explanation

This is triggered automatically whenever a pool calls `forward_pool_reward` on a `PLOTNFT_2` wallet's pool reward coin and the spend is synced by the wallet. Any user who has joined a pool using the `PLOTNFT_2` wallet type and has had at least one reward forwarded by the pool will be affected. No special attacker capability is required — the pool is the normal privileged actor performing the expected operation.

### Recommendation

Add `await self.coin_store.set_spent(coin_name, uint32(coin_state.spent_height))` in the `else` branch of the `WalletType.PLOTNFT_2` block, mirroring the singleton branch:

```python
elif record.wallet_type == WalletType.PLOTNFT_2:
    if isinstance(coin_data, PlotNFT):
        await self.coin_added(...)
        await self.coin_store.set_spent(coin_name, uint32(coin_state.spent_height))
        await self.add_interested_coin_ids([coin_name])
    else:
        await self.plotnft2_store.mark_pool_reward_as_spent(
            reward_id=coin_name,
            spent_height=coin_state.spent_height,
        )
        await self.coin_store.set_spent(coin_name, uint32(coin_state.spent_height))  # ADD THIS
```

### Proof of Concept

1. Create a `PLOTNFT_2` wallet and join a pool.
2. Farm blocks so pool reward coins accumulate at `p2_singleton_puzzle_hash`.
3. Have the pool call `forward_pool_reward` on one of those coins and confirm the spend.
4. Query `plotnft_wallet.get_confirmed_balance()` — it returns the forwarded reward amount as if it were still unspent.
5. Query `wallet_state_manager.coin_store.get_unspent_coins_for_wallet(plotnft_wallet.id())` — the forwarded pool reward coin is still present as unspent.
6. Query `plotnft2_store.get_pool_rewards(plotnft_id=..., include_spent=False)` — the reward is correctly absent (plotnft2_store was updated). The divergence between the two stores confirms the missing `set_spent` call. [6](#0-5)

### Citations

**File:** chia/wallet/wallet_state_manager.py (L2140-2158)
```python
                        elif record.wallet_type == WalletType.PLOTNFT_2:
                            if isinstance(coin_data, PlotNFT):
                                await self.coin_added(
                                    coin_state.coin,
                                    uint32(coin_state.created_height),
                                    all_unconfirmed,
                                    wallet_identifier.id,
                                    wallet_identifier.type,
                                    peer,
                                    coin_name,
                                    coin_data,
                                )
                                await self.coin_store.set_spent(coin_name, uint32(coin_state.spent_height))
                                await self.add_interested_coin_ids([coin_name])
                            else:
                                await self.plotnft2_store.mark_pool_reward_as_spent(
                                    reward_id=coin_name,
                                    spent_height=coin_state.spent_height,
                                )
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L464-470)
```python
        elif coin_data is None and coin.puzzle_hash == self.p2_singleton_puzzle_hash:
            if coin.parent_coin_info[0:16] == self.wallet_state_manager.constants.GENESIS_CHALLENGE[0:16]:
                await self.wallet_state_manager.plotnft2_store.add_pool_reward(
                    pool_reward=PoolReward(singleton_id=self.plotnft_id, coin=coin)
                )
            else:
                raise ValueError(f"A non-pooling reward coin was paid to PlotNFT with id: {self.plotnft_id}")
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L488-502)
```python
    async def get_confirmed_balance(self, record_list: set[WalletCoinRecord] | None = None) -> uint128:
        return uint128(
            sum(
                cr.coin.amount
                for cr in await self.wallet_state_manager.coin_store.get_unspent_coins_for_wallet(self.id())
                if cr.coin.amount != 1  # bit of a hack, but should work well enough to filter out the plotnft
            )
        )

    async def get_unconfirmed_balance(self, unspent_records: set[WalletCoinRecord] | None = None) -> uint128:
        # bit of a hack, but should work well enough to filter out the plotnft
        if unspent_records is None:
            unspent_records = await self.wallet_state_manager.coin_store.get_unspent_coins_for_wallet(self.id())
        unspent_records = set(cr for cr in unspent_records if cr.coin.amount != 1)

```

**File:** chia/wallet/plotnft_wallet/plotnft_store.py (L130-135)
```python
    async def mark_pool_reward_as_spent(self, *, reward_id: bytes32, spent_height: uint32) -> None:
        async with self.db_wrapper.writer_maybe_transaction() as conn:
            await conn.execute_insert(
                "UPDATE pool_reward2s SET spent_height = ? WHERE coin_id = ?",
                (spent_height, reward_id),
            )
```
