Looking at the exact code paths involved, I need to verify each step of the claimed exploit chain.

**Step 1: `rollback_to_block` does not clear `finish_exiting_height`** [1](#0-0) 

The rollback only touches `plotnft2s` and `pool_reward2s`. The `finish_exiting_height` and `finish_exiting_info` tables are completely untouched.

**Step 2: `finish_exiting_height` is set when the exiting coin is observed** [2](#0-1) 

When the exiting PlotNFT coin is added at `H_leave`, `finish_exiting_height` is set to `H_leave + heightlock`.

**Step 3: After reorg to `H_fork` (where `H_join <= H_fork < H_leave`), the exiting row is deleted but `finish_exiting_height` remains**

The `coin_added` path that could clear it has a guard: [3](#0-2) 

After the reorg, if `coin_added` is re-invoked for the pooling coin at `H_join`, the condition is `finish_height < height`, i.e., `(H_leave + heightlock) < H_join`. Since `H_leave > H_join`, this is **always FALSE** — `clear_exiting_info` is never called.

**Step 4: `new_peak` fires and calls `_finish_leaving_pool`** [4](#0-3) 

No guard checks whether the current PlotNFT is actually in the exiting state before calling `_finish_leaving_pool`.

**Step 5: `_finish_leaving_pool` calls `exit_waiting_room` on a non-exiting PlotNFT** [5](#0-4) 

`get_current_plotnft()` returns the pooling (non-exiting) PlotNFT (the only row left after rollback). Then `exit_waiting_room` is called on it: [6](#0-5) 

Since `self.exiting` is `False` for the pooling PlotNFT, this raises `ValueError("Cannot exit waiting room while not in it")` unconditionally. There is no try/except in `new_peak` or `_finish_leaving_pool` to catch this.

**Step 6: The wallet is permanently stuck**

`finish_exiting_height` is never cleared (rollback doesn't touch it, `coin_added` condition is never satisfied), so every subsequent `new_peak` at height `>= H_leave + heightlock - 2` will re-enter this failing path indefinitely.

---

### Title
Post-Reorg `finish_exiting_height` Not Cleared Causes Permanent Wallet Stuck State — (`chia/wallet/plotnft_wallet/plotnft_store.py`, `plotnft_wallet.py`)

### Summary
`PlotNFTStore.rollback_to_block` omits clearing the `finish_exiting_height` and `finish_exiting_info` tables. After a reorg that reverts past a `leave_pool` transaction but not past the preceding `join_pool`, the wallet permanently retains a stale `finish_exiting_height`. Every subsequent `new_peak` at the trigger height calls `_finish_leaving_pool` → `exit_waiting_room` on a non-exiting PlotNFT, raising an unhandled `ValueError`. The wallet is permanently unable to complete or retry pool operations.

### Finding Description
`rollback_to_block` (lines 258–262 of `plotnft_store.py`) deletes rows from `plotnft2s` and `pool_reward2s` but never touches `finish_exiting_height` or `finish_exiting_info`. After a reorg to `H_fork` where `H_join <= H_fork < H_leave`:

- The exiting PlotNFT row (`created_height = H_leave`) is deleted.
- The pooling PlotNFT row (`created_height = H_join`) survives.
- `finish_exiting_height = H_leave + heightlock` survives.

The `coin_added` cleanup path (lines 461–463) only clears `finish_exiting_height` if `finish_height < height`, which is `(H_leave + heightlock) < H_join` — always false. So the stale height is never cleared.

When `new_peak` fires at `>= H_leave + heightlock - 2`, it calls `_finish_leaving_pool`, which calls `get_current_plotnft()` (returning the pooling, non-exiting PlotNFT), then `exit_waiting_room`, which raises `ValueError("Cannot exit waiting room while not in it")` at line 582 of `plotnft_drivers.py`. No exception handler exists in `new_peak` or `_finish_leaving_pool`.

### Impact Explanation
The wallet is permanently stuck: `finish_exiting_height` is never cleared, so every `new_peak` at the trigger height raises an unhandled exception. Pool operations (leave, join, claim rewards) are blocked indefinitely. Recovery requires direct database manipulation — not possible through any normal wallet API.

### Likelihood Explanation
Requires a reorg deep enough to revert `H_leave` but not `H_join`. Natural reorgs of this depth are uncommon but not impossible; a well-resourced adversary with sufficient hash power can deliberately trigger one. The precondition (a PlotNFT mid-exit-process) is a normal operational state for any pool participant.

### Recommendation
In `rollback_to_block`, also clear or conditionally reset `finish_exiting_height` and `finish_exiting_info` for any wallet whose stored `finish_exiting_height` was derived from a block height `> height`:

```python
# In rollback_to_block, after the existing DELETEs:
await conn.execute(
    "DELETE FROM finish_exiting_height WHERE height > ?", (height + MAX_HEIGHTLOCK,)
)
# Or more precisely: join against plotnft2s to find wallets whose exiting row was rolled back
```

Additionally, add a guard in `_finish_leaving_pool` (or `new_peak`) that checks `plotnft.exiting` before proceeding, and clears `finish_exiting_height` if the current PlotNFT is not in the exiting state.

### Proof of Concept
1. Launch a PlotNFT wallet and join a pool at block `H_join`. Record `finish_exiting_height = None`.
2. Call `leave_pool` at block `H_leave`. Confirm `finish_exiting_height = H_leave + heightlock`.
3. Trigger a reorg to `H_fork` where `H_join <= H_fork < H_leave` (via simulator `reorg_from_index`).
4. Assert: `plotnft2s` has only the pooling row; `finish_exiting_height` is still set.
5. Advance the chain to `H_leave + heightlock`. Observe `new_peak` raises `ValueError("Cannot exit waiting room while not in it")`.
6. Assert: `finish_exiting_height` is still set; wallet cannot perform any pool operation.

### Citations

**File:** chia/wallet/plotnft_wallet/plotnft_store.py (L258-262)
```python
    async def rollback_to_block(self, *, height: int) -> None:
        async with self.db_wrapper.writer_maybe_transaction() as conn:
            await conn.execute("DELETE FROM plotnft2s WHERE created_height > ?", (height,))
            await conn.execute("DELETE FROM pool_reward2s WHERE height > ?", (height,))
            await conn.execute("UPDATE pool_reward2s SET spent_height = NULL WHERE spent_height > ?", (height,))
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L353-363)
```python
        plotnft = await self.get_current_plotnft()
        fee_hook = CreateCoinAnnouncement(msg=b"", coin_id=plotnft.coin.name())
        heightlock, exit_create_coin = plotnft.exit_from_waiting_room_conditions()
        exit_to_waiting_room_dpuz_and_sol = DelegatedPuzzleAndSolution(
            puzzle=self.xch_wallet.make_solution(
                primaries=[exit_create_coin],
                conditions=(fee_hook, heightlock, *extra_conditions),
            ).at("rf"),  # strips away to just the delegated puzzle (bit of a hack)
            solution=Program.to(None),
        )
        coin_spends = plotnft.exit_waiting_room(exit_to_waiting_room_dpuz_and_sol)
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L456-459)
```python
            if coin_data.exiting:
                await self.wallet_state_manager.plotnft2_store.add_exiting_height(
                    wallet_id=self.id(), height=uint32(height + coin_data.guaranteed_pool_config.heightlock)
                )
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L461-463)
```python
                finish_height = await self.wallet_state_manager.plotnft2_store.get_exiting_height(wallet_id=self.id())
                if finish_height is not None and finish_height < height:
                    await self.wallet_state_manager.plotnft2_store.clear_exiting_info(wallet_id=self.id())
```

**File:** chia/wallet/plotnft_wallet/plotnft_wallet.py (L472-482)
```python
    async def new_peak(self, height: uint32) -> None:
        finish_height = await self.wallet_state_manager.plotnft2_store.get_exiting_height(wallet_id=self.id())
        if finish_height is not None and finish_height <= height - 2:  # 2 blocks for a little reorg safety
            if await self.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(wallet_id=self.id()) != []:
                self.log.info(f"Not finishing plotnft from wallet {self.id()} due to unconfirmed transactions")
                return None
            finish_info = await self.wallet_state_manager.plotnft2_store.get_exiting_info(wallet_id=self.id())
            async with self.wallet_state_manager.new_action_scope(
                self.wallet_state_manager.tx_config, push=True, sign=True, merge_spends=True
            ) as action_scope:
                await self._finish_leaving_pool(action_scope=action_scope, exiting_info=finish_info)
```

**File:** chia/pools/plotnft_drivers.py (L578-582)
```python
    def exit_waiting_room(self, delegated_puzzle_and_solution: DelegatedPuzzleAndSolution) -> list[CoinSpend]:
        if not self.pooling:
            raise ValueError("Cannot exit waiting room while self pooling.")
        if not self.exiting:
            raise ValueError("Cannot exit waiting room while not in it")
```
