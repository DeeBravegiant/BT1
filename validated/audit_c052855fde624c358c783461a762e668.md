### Title
Permissionless Pool Absorb Spend Resets Singleton Coin Height, Permanently Blocking Pool Exit - (File: chia/pools/pool_puzzles.py, chia/pools/pool_wallet.py)

### Summary
An unprivileged attacker can permanently prevent a farmer from completing a pool exit (`LEAVING_POOL → SELF_POOLING` or `LEAVING_POOL → FARMING_TO_POOL`) by repeatedly submitting permissionless absorb spends against the singleton while it is in the `LEAVING_POOL` (waiting room) state. Each absorb spend recreates the singleton coin at a new block height, resetting the `ASSERT_HEIGHT_RELATIVE` countdown enforced by the CLVM waiting room puzzle, and also resetting the wallet-level lock-height check in `join_pool()`, `self_pool()`, and `new_peak()`.

### Finding Description

**Permissionless absorb spend in LEAVING_POOL state**

`create_absorb_spend()` in `chia/pools/pool_puzzles.py` constructs a spend bundle for both the `FARMING_TO_POOL` (pool member) and `LEAVING_POOL` (waiting room) states. When `fee=0`, the bundle is signed with `G2Element()` — an empty aggregate signature — meaning no private key is required: [1](#0-0) 

The test suite confirms this works in the waiting room state with no signature: [2](#0-1) 

**Absorb spend updates the singleton tip height**

`get_tip()` returns the last entry in the pool store, which includes absorb spends: [3](#0-2) 

`apply_state_transition()` adds every confirmed singleton spend (including absorb spends) to the pool store: [4](#0-3) 

**Wallet-level lock check uses tip height from absorb spend**

`new_peak()` computes `leave_height` using `tip_height` from `get_tip()`: [5](#0-4) 

`join_pool()` and `self_pool()` use `history[-1][0]` (the last spend's height, which is the absorb spend after an attacker acts) for the lock check: [6](#0-5) [7](#0-6) 

**CLVM-level enforcement is also reset**

The waiting room inner puzzle emits `ASSERT_HEIGHT_RELATIVE(relative_lock_height)` when the second travel spend is attempted. This condition is evaluated relative to the **coin being spent** — i.e., the singleton coin recreated by the absorb spend. After an absorb spend at block `H'`, the new singleton coin's confirmed height is `H'`, so the CLVM puzzle requires the second travel spend to be included at or after block `H' + relative_lock_height`. The test confirms this enforcement: [8](#0-7) 

### Impact Explanation

An attacker can permanently prevent a farmer from leaving a pool:

1. Farmer submits first travel spend at block `H`, entering `LEAVING_POOL` state with `relative_lock_height = R`.
2. Attacker monitors the chain. Just before block `H + R`, attacker constructs and submits a valid absorb spend (no signature required, all inputs are public on-chain data).
3. Absorb spend is confirmed at block `H + R - 1`. The singleton coin is recreated at height `H + R - 1`.
4. The CLVM `ASSERT_HEIGHT_RELATIVE(R)` now requires the second travel spend to be included at or after block `(H + R - 1) + R`. The wallet's `new_peak()` and `join_pool()`/`self_pool()` checks are similarly reset.
5. Attacker repeats indefinitely at zero cost (fee=0 absorb spends require no XCH).

The farmer is permanently locked in the pool, unable to reclaim self-pooling rewards or switch pools. This constitutes a **High** impact: long-lived inability for a farmer to execute valid pool state transitions, and corruption of pool membership state with direct security impact (rewards continue flowing to the pool's `target_puzzle_hash` rather than the farmer's wallet).

### Likelihood Explanation

- All inputs needed to construct the absorb spend are publicly visible on-chain (singleton coin, puzzle reveal, p2_singleton reward coin).
- No keys, admin access, or special privileges are required.
- The attack costs only standard transaction fees (zero if fee=0).
- The attacker only needs to act once per `relative_lock_height` window (minimum 5 blocks, maximum 1000 blocks per `MINIMUM_RELATIVE_LOCK_HEIGHT` / `MAXIMUM_RELATIVE_LOCK_HEIGHT`). [9](#0-8) 

### Recommendation

The relative lock height for the pool exit should be measured from the height of the **last state-changing spend** (the LEAVING_POOL travel spend), not from the height of the last spend of any kind (which includes absorb spends). Concretely:

- In `new_peak()`, replace `tip_height` with `pool_wallet_info.singleton_block_height` (the `last_singleton_spend_height` from `get_current_state()`).
- In `join_pool()` and `self_pool()`, replace `history[-1][0]` with the height of the last spend for which `solution_to_pool_state()` returns a non-None value.
- At the CLVM level, consider whether the waiting room puzzle should anchor `ASSERT_HEIGHT_RELATIVE` to the height at which the LEAVING_POOL state was first entered rather than the height of the coin being spent. This may require a puzzle redesign or an additional committed value.

### Proof of Concept

```
Block H:   Farmer submits travel spend → singleton enters LEAVING_POOL (relative_lock_height=10)
           Singleton coin confirmed at height H.

Block H+9: Attacker constructs absorb spend using public on-chain data:
             - current singleton coin (spent at H)
             - p2_singleton reward coin at p2_singleton_puzzle_hash
           Submits SpendBundle([singleton_spend, reward_spend], G2Element())
           → accepted with no signature (fee=0)
           → singleton coin recreated at height H+9

Block H+9: Farmer's second travel spend fails:
           ASSERT_HEIGHT_RELATIVE(10) requires coin confirmed at H+9,
           spend must be in block >= H+9+10 = H+19.
           Wallet's new_peak(): leave_height = (H+9) + 10 = H+19 > peak_height.

Block H+18: Attacker repeats absorb spend → singleton coin at H+18.
            Lock resets to H+28. Farmer permanently blocked.
```

### Citations

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

**File:** chia/_tests/pools/test_pool_puzzles_lifecycle.py (L326-333)
```python
        # Spend it and hope it fails!
        with pytest.raises(
            BadSpendBundleError,
            match=re.escape(f"condition validation failure {Err.ASSERT_HEIGHT_RELATIVE_FAILED!s}"),
        ):
            coin_db.update_coin_store_for_spend_bundle(
                SpendBundle([return_coinsol], sig), time, DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM
            )
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

**File:** chia/pools/pool_wallet.py (L68-71)
```python
    MINIMUM_INITIAL_BALANCE: ClassVar[int] = 1
    MINIMUM_RELATIVE_LOCK_HEIGHT: ClassVar[int] = 5
    MAXIMUM_RELATIVE_LOCK_HEIGHT: ClassVar[int] = 1000
    DEFAULT_MAX_CLAIM_SPENDS: ClassVar[int] = 100
```

**File:** chia/pools/pool_wallet.py (L228-229)
```python
    async def get_tip(self) -> tuple[uint32, CoinSpend]:
        return (await self.wallet_state_manager.pool_store.get_spends_for_wallet(self.wallet_id))[-1]
```

**File:** chia/pools/pool_wallet.py (L286-288)
```python
        await self.wallet_state_manager.pool_store.add_spend(self.wallet_id, new_state, block_height)
        tip_spend = (await self.get_tip())[1]
        self.log.info(f"New PoolWallet singleton tip_coin: {tip_spend} farmed at height {block_height}")
```

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
