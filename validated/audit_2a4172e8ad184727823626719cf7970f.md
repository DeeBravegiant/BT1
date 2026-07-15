### Title
Pool Singleton Absorb Spend Resets `relative_lock_height` Countdown, Enabling Permanent DoS of Pool Exit - (File: `chia/pools/pool_wallet.py`, `chia/pools/pool_puzzles.py`)

### Summary

The pool singleton's absorb-reward spend path is permissionless (requires no owner signature) and can be executed by anyone — including a malicious pool — while the singleton is in `LEAVING_POOL` state. Each absorb spend creates a new singleton coin, which resets the `ASSERT_HEIGHT_RELATIVE` countdown enforced by the waiting-room CLVM puzzle. The wallet's lock-height check in `join_pool()`, `self_pool()`, and `new_peak()` also uses `history[-1][0]` (the most recent spend height, including absorbs) rather than the height of the `LEAVING_POOL` transition. A pool that continuously absorbs farming rewards while a farmer is trying to leave can permanently prevent the farmer from completing the exit.

### Finding Description

When a farmer initiates a pool exit, the singleton transitions to `LEAVING_POOL` state. The farmer must wait `relative_lock_height` blocks before completing the exit. This countdown is enforced at two levels:

**1. CLVM level (`pool_waiting_room_innerpuz.clsp`):** The exit path emits `ASSERT_HEIGHT_RELATIVE(relative_lock_height)`, which is evaluated relative to the block height at which the **current singleton coin** was created. Each absorb spend destroys the current singleton coin and creates a new one, so the `ASSERT_HEIGHT_RELATIVE` countdown resets to the absorb block's height.

**2. Wallet level (`pool_wallet.py`):** In `join_pool()` and `self_pool()`, the check uses `history[-1][0]` — the height of the most recent spend in the pool store, which includes absorb spends:

```python
history: list[tuple[uint32, CoinSpend]] = await self.get_spend_history()
last_height: uint32 = history[-1][0]
if (
    await self.wallet_state_manager.blockchain.get_finished_sync_up_to()
    <= last_height + current_state.current.relative_lock_height
):
    raise ValueError(...)
```

In `new_peak()`, the same pattern appears:

```python
tip_height, tip_spend = await self.get_tip()
leave_height = tip_height + pool_wallet_info.current.relative_lock_height
```

The absorb spend itself is constructed with an **empty signature** (`G2Element()`), confirmed by the lifecycle test:

```python
coin_db.update_coin_store_for_spend_bundle(
    SpendBundle(coin_sols, G2Element()), time, ...
)
```

This means any party — not just the pool — can submit a valid absorb spend for any farming reward that lands at the `p2_singleton_puzzle_hash`, which is a public, deterministic address.

### Impact Explanation

A malicious pool (or any third party) can:
1. Monitor the `p2_singleton_puzzle_hash` for incoming farming rewards
2. Continuously submit absorb spends for those rewards while the farmer is in `LEAVING_POOL` state
3. Each absorb resets the `relative_lock_height` countdown at both the CLVM and wallet levels
4. As long as the farmer's plots continue to win blocks (producing rewards), the exit can be delayed indefinitely

The farmer's rewards continue to be forwarded to the pool's puzzle hash during this period. The farmer cannot prevent absorb spends — they are permissionless by protocol design. This constitutes a **permanent or long-lived inability for honest farmers to process valid pool actions**, matching the High impact threshold.

### Likelihood Explanation

The pool has a direct financial incentive to prevent a farmer from leaving (retaining farming rewards). The pool already runs infrastructure to submit absorb spends. The attack costs only transaction fees. The farmer has no on-chain recourse. The `p2_singleton_puzzle_hash` is public and the reward coin parameters (`pool_parent_id`, `calculate_pool_reward`) are fully deterministic, so any observer can construct and submit absorb spends.

### Recommendation

The `relative_lock_height` countdown should be anchored to the block height at which the `LEAVING_POOL` state was entered, not the height of the most recent singleton spend. Concretely:

- In `join_pool()` and `self_pool()`, replace `history[-1][0]` with the height of the spend that introduced the `LEAVING_POOL` state (i.e., the height returned by `get_current_state()` as `last_singleton_spend_height`).
- In `new_peak()`, replace `tip_height` with the same anchored height.
- At the CLVM level, consider whether the waiting-room puzzle should use an absolute height assertion (`ASSERT_HEIGHT_ABSOLUTE`) anchored to the transition block rather than `ASSERT_HEIGHT_RELATIVE` on the current coin.

### Proof of Concept

```
1. Farmer joins pool with relative_lock_height = 500.
2. Farmer calls self_pool() at block H=1000 → singleton enters LEAVING_POOL.
3. Farmer's plots farm a reward at block H=1200 → reward coin lands at p2_singleton_puzzle_hash.
4. Pool (or attacker) calls create_absorb_spend() with no signature and submits it.
   → New singleton coin created at H=1200. Lock countdown resets: must wait until H=1700.
5. Farmer's plots farm another reward at H=1650 → attacker absorbs again.
   → New singleton coin at H=1650. Must wait until H=2150.
6. Repeat indefinitely. Farmer never reaches the exit condition.
```

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** chia/pools/pool_wallet.py (L657-666)
```python
        if current_state.current.state == LEAVING_POOL.value:
            history: list[tuple[uint32, CoinSpend]] = await self.get_spend_history()
            last_height: uint32 = history[-1][0]
            if (
                await self.wallet_state_manager.blockchain.get_finished_sync_up_to()
                <= last_height + current_state.current.relative_lock_height
            ):
                raise ValueError(
                    f"Cannot join a pool until height {last_height + current_state.current.relative_lock_height}"
                )
```

**File:** chia/pools/pool_wallet.py (L693-703)
```python
        if current_state.current.state == LEAVING_POOL.value:
            total_fee = fee
            history: list[tuple[uint32, CoinSpend]] = await self.get_spend_history()
            last_height: uint32 = history[-1][0]
            if (
                await self.wallet_state_manager.blockchain.get_finished_sync_up_to()
                <= last_height + current_state.current.relative_lock_height
            ):
                raise ValueError(
                    f"Cannot self pool until height {last_height + current_state.current.relative_lock_height}"
                )
```

**File:** chia/pools/pool_wallet.py (L819-826)
```python
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
