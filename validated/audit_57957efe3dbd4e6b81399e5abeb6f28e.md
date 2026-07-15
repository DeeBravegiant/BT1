### Title
Pool Lockdown Period Reset via Absorb Spend During LEAVING\_POOL State - (File: chia/pools/pool_wallet.py)

### Summary

When a farmer initiates a pool exit (transitioning to `LEAVING_POOL`), the wallet's `new_peak()` method computes the lockdown expiry as `tip_height + relative_lock_height`, where `tip_height` is the height of the most recent singleton spend. Because absorb spends are explicitly permitted while the singleton is in the waiting-room (`LEAVING_POOL`) state, any party can absorb a p2\_singleton reward during the lockdown window, which advances `tip_height` to the absorb block and restarts the countdown. A pool (or any third party) that continuously absorbs rewards can prevent the farmer from ever completing the exit.

### Finding Description

`new_peak()` in `chia/pools/pool_wallet.py` determines when the second travel transaction (completing the exit) may be submitted:

```python
tip_height, tip_spend = await self.get_tip()
leave_height = tip_height + pool_wallet_info.current.relative_lock_height
if peak_height > leave_height + 2:
    ...
    await self.generate_travel_transactions(...)
```

`get_tip()` returns the height of the **most recent singleton spend** stored in the pool store. Every time `apply_state_transition` is called with a new coin spend, it calls `pool_store.add_spend(self.wallet_id, new_state, block_height)`, updating the tip to the new block height. [1](#0-0) 

`create_absorb_spend` in `pool_puzzles.py` explicitly handles the waiting-room (LEAVING\_POOL) inner puzzle:

```python
elif is_pool_waitingroom_inner_puzzle(inner_puzzle):
    # inner sol is (spend_type, destination_puzhash, pool_reward_amount, pool_reward_height, extra_data)
    inner_sol = Program.to([0, reward_amount, height])
``` [2](#0-1) 

This is confirmed by the lifecycle test, which explicitly exercises "ABSORB WHILE IN WAITING ROOM": [3](#0-2) 

The absorb spend spends the current singleton coin and recreates it with the same `LEAVING_POOL` state but a new coin ID at the current block height. `apply_state_transition` then records this new spend as the tip: [4](#0-3) 

### Impact Explanation

Each absorb spend while in `LEAVING_POOL` state advances `tip_height` to the absorb block, so `leave_height = tip_height + relative_lock_height` is pushed forward by the same amount. As long as the farmer's plots continue farming (which they do, since the exit is not yet complete), new p2\_singleton rewards accumulate, giving the pool (or any third party) fresh opportunities to absorb and reset the countdown. The farmer can never satisfy `peak_height > leave_height + 2` and is permanently locked in the pool, with all block rewards continuing to flow to the pool's `target_puzzle_hash`.

This maps to: **High — Permanent or long-lived inability for honest farmers to process valid pool actions (pool exit) under normal network assumptions**, and **High — Corruption of pool membership state / payout redirection with direct security impact**.

### Likelihood Explanation

The pool has a direct financial incentive to keep the farmer locked in. The absorb spend in the waiting room requires no signature from the farmer or pool owner — it is permissionless, requiring only knowledge of the singleton state (which is public on-chain). The pool already monitors the singleton as part of normal operations. Executing repeated absorbs costs only transaction fees, which are negligible compared to the farming rewards retained.

### Recommendation

Compute the lockdown expiry from the height at which the singleton **first entered** `LEAVING_POOL` state, not from the tip of the most recent singleton spend. Store the `LEAVING_POOL` entry height separately (e.g., in the pool store alongside the spend history) and use it as the fixed reference point:

```python
# Use the height at which LEAVING_POOL was first confirmed, not tip_height
leave_height = leaving_pool_entry_height + pool_wallet_info.current.relative_lock_height
```

This mirrors the recommendation in the external report: do not reset the lockdown period on intermediate spends; keep it anchored to the original exit declaration.

### Proof of Concept

1. Farmer joins pool with `relative_lock_height = 100`.
2. At block H, farmer calls `pw_self_pool` → singleton transitions to `LEAVING_POOL`. `tip_height = H`.
3. `new_peak` computes `leave_height = H + 100`. Farmer must wait until block H+102.
4. At block H+10, a p2\_singleton reward exists. Pool (or anyone) calls `create_absorb_spend` with the waiting-room inner puzzle. The absorb spend is accepted on-chain (no signature required beyond the p2\_singleton puzzle). `apply_state_transition` records the new singleton spend at height H+10. `tip_height = H+10`.
5. `new_peak` now computes `leave_height = H+10 + 100 = H+110`. The farmer must wait until H+112.
6. Repeat step 4 every ~10 blocks. The farmer's exit is perpetually deferred. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** chia/pools/pool_wallet.py (L261-302)
```python
    async def apply_state_transition(
        self, new_state: CoinSpend, block_height: uint32, action_scope: WalletActionScope
    ) -> bool:
        """
        Updates the Pool state (including DB) with new singleton spends.
        The DB must be committed after calling this method. All validation should be done here. Returns True iff
        the spend is a valid transition spend for the singleton, False otherwise.
        """
        tip: tuple[uint32, CoinSpend] = await self.get_tip()
        tip_spend = tip[1]

        tip_coin: Coin | None = get_most_recent_singleton_coin_from_coin_spend(tip_spend)
        assert tip_coin is not None
        spent_coin_name: bytes32 = tip_coin.name()

        if spent_coin_name != new_state.coin.name():
            history: list[tuple[uint32, CoinSpend]] = await self.get_spend_history()
            if new_state.coin.name() in [sp.coin.name() for _, sp in history]:
                self.log.info(f"Already have state transition: {new_state.coin.name().hex()}")
            else:
                self.log.warning(
                    f"Failed to apply state transition. tip: {tip_coin} new_state: {new_state} height {block_height}"
                )
            return False

        await self.wallet_state_manager.pool_store.add_spend(self.wallet_id, new_state, block_height)
        tip_spend = (await self.get_tip())[1]
        self.log.info(f"New PoolWallet singleton tip_coin: {tip_spend} farmed at height {block_height}")

        # If we have reached the target state, resets it to None. Loops back to get current state
        for _, added_spend in reversed(
            await self.wallet_state_manager.pool_store.get_spends_for_wallet(self.wallet_id)
        ):
            latest_state: PoolState | None = solution_to_pool_state(added_spend)
            if latest_state is not None:
                if self.target_state == latest_state:
                    self.target_state = None
                    self.next_transaction_fee = uint64(0)
                    self.next_tx_config = DEFAULT_TX_CONFIG
                break

        await self.update_pool_config(action_scope)
```

**File:** chia/pools/pool_wallet.py (L807-826)
```python
    async def new_peak(self, peak_height: uint32) -> None:
        # This gets called from the WalletStateManager whenever there is a new peak

        pool_wallet_info: PoolWalletInfo = await self.get_current_state()
        tip_height, tip_spend = await self.get_tip()

        if self.target_state is None:
            return
        if self.target_state == pool_wallet_info.current:
            self.target_state = None
            raise ValueError(f"Internal error. Pool wallet {self.wallet_id} state: {pool_wallet_info.current}")

        if (
            self.target_state.state in {FARMING_TO_POOL.value, SELF_POOLING.value}
            and pool_wallet_info.current.state == LEAVING_POOL.value
        ):
            leave_height = tip_height + pool_wallet_info.current.relative_lock_height

            # Add some buffer (+2) to reduce chances of a reorg
            if peak_height > leave_height + 2:
```

**File:** chia/pools/pool_puzzles.py (L252-308)
```python
def create_absorb_spend(
    last_coin_spend: CoinSpend,
    current_state: PoolState,
    launcher_coin: Coin,
    height: uint32,
    genesis_challenge: bytes32,
    delay_time: uint64,
    delay_ph: bytes32,
) -> list[CoinSpend]:
    inner_puzzle: Program = pool_state_to_inner_puzzle(
        current_state, launcher_coin.name(), genesis_challenge, delay_time, delay_ph
    )
    reward_amount: uint64 = calculate_pool_reward(height)
    if is_pool_member_inner_puzzle(inner_puzzle):
        # inner sol is (spend_type, pool_reward_amount, pool_reward_height, extra_data)
        inner_sol: Program = Program.to([reward_amount, height])
    elif is_pool_waitingroom_inner_puzzle(inner_puzzle):
        # inner sol is (spend_type, destination_puzhash, pool_reward_amount, pool_reward_height, extra_data)
        inner_sol = Program.to([0, reward_amount, height])
    else:
        raise ValueError
    # full sol = (parent_info, my_amount, inner_solution)
    coin: Coin | None = get_most_recent_singleton_coin_from_coin_spend(last_coin_spend)
    assert coin is not None

    if coin.parent_coin_info == launcher_coin.name():
        parent_info: Program = Program.to([launcher_coin.parent_coin_info, launcher_coin.amount])
    else:
        p = Program.from_bytes(bytes(last_coin_spend.puzzle_reveal))
        last_coin_spend_inner_puzzle: Program | None = get_inner_puzzle_from_puzzle(p)
        assert last_coin_spend_inner_puzzle is not None
        parent_info = Program.to(
            [
                last_coin_spend.coin.parent_coin_info,
                last_coin_spend_inner_puzzle.get_tree_hash(),
                last_coin_spend.coin.amount,
            ]
        )
    full_solution: SerializedProgram = SerializedProgram.to([parent_info, last_coin_spend.coin.amount, inner_sol])
    full_puzzle: SerializedProgram = create_full_puzzle(inner_puzzle, launcher_coin.name()).to_serialized()
    assert coin.puzzle_hash == full_puzzle.get_tree_hash()

    reward_parent: bytes32 = pool_parent_id(height, genesis_challenge)
    p2_singleton_puzzle = create_p2_singleton_puzzle(
        SINGLETON_MOD_HASH, launcher_coin.name(), delay_time, delay_ph
    ).to_serialized()
    reward_coin: Coin = Coin(reward_parent, p2_singleton_puzzle.get_tree_hash(), reward_amount)
    p2_singleton_solution = SerializedProgram.to([inner_puzzle.get_tree_hash(), reward_coin.name()])
    assert p2_singleton_puzzle.get_tree_hash() == reward_coin.puzzle_hash
    assert full_puzzle.get_tree_hash() == coin.puzzle_hash
    assert get_inner_puzzle_from_puzzle(Program.from_bytes(bytes(full_puzzle))) is not None

    coin_spends = [
        CoinSpend(coin, full_puzzle, full_solution),
        CoinSpend(reward_coin, p2_singleton_puzzle, p2_singleton_solution),
    ]
    return coin_spends
```

**File:** chia/_tests/pools/test_pool_puzzles_lifecycle.py (L335-352)
```python
        # ABSORB WHILE IN WAITING ROOM
        time = CoinTimestamp(10000060, 3)
        # create the farming reward
        coin_db.farm_coin(p2_singleton_ph, time, 1750000000000)
        # generate relevant coin solutions
        coin_sols: list[CoinSpend] = create_absorb_spend(
            travel_coinsol,
            target_pool_state,
            launcher_coin,
            3,
            GENESIS_CHALLENGE,
            DELAY_TIME,
            DELAY_PH,  # height
        )
        # Spend it!
        coin_db.update_coin_store_for_spend_bundle(
            SpendBundle(coin_sols, G2Element()), time, DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM
        )
```
