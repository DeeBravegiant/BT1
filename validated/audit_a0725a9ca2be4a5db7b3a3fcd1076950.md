### Title
Pool Waiting Room `ASSERT_HEIGHT_RELATIVE` Countdown Reset via Permissionless Absorb Spend - (`File: chia/pools/pool_puzzles.py`)

### Summary

A malicious pool operator can indefinitely prevent a farmer from leaving the pool by repeatedly submitting permissionless absorb spends while the farmer's singleton is in the `LEAVING_POOL` (waiting room) state. Each absorb spend creates a new singleton coin, resetting the `ASSERT_HEIGHT_RELATIVE` countdown that gates the farmer's exit. This is a direct analog to H-15: just as GMX order updates reset the block-number gate on execution, pool absorb spends reset the block-height gate on pool exit.

### Finding Description

The pool singleton protocol enforces a mandatory waiting period before a farmer can exit a pool. When a farmer transitions from `FARMING_TO_POOL` to `LEAVING_POOL`, the singleton enters the waiting room inner puzzle (`POOL_WAITINGROOM_INNERPUZ`), which enforces `ASSERT_HEIGHT_RELATIVE = relative_lock_height` on the exit spend path. This condition is evaluated against the **confirmed block index of the coin being spent** — i.e., the birth height of the current waiting room singleton coin.

The waiting room puzzle supports two spend paths:

1. **Exit (spend_type=1)**: Transitions the singleton out of the waiting room. Requires `ASSERT_HEIGHT_RELATIVE` blocks to have passed since the current coin's birth. Requires the owner's signature.
2. **Absorb (spend_type=0)**: Claims a pool reward coin and re-creates the singleton in the same waiting room state. **Requires no signature** — confirmed by the test using `G2Element()` (empty signature). [1](#0-0) 

When `create_absorb_spend` is called in the waiting room state, it spends the current singleton coin and creates a new one: [2](#0-1) 

The new singleton coin is born at the current block height. Because `ASSERT_HEIGHT_RELATIVE` is measured from the **new coin's birth height**, the entire `relative_lock_height` countdown restarts from zero. The test suite confirms this behavior — after an absorb at height 3, the farmer must wait until height 10000+ (well beyond the original `relative_lock_height` of 5000): [3](#0-2) 

The absorb spend requires only a valid pool reward coin (a deterministic coin whose parent is `pool_parent_id(height, genesis_challenge)`) and the current singleton coin — both of which are publicly observable on-chain. No farmer authorization is needed: [4](#0-3) 

### Impact Explanation

A malicious pool operator can:

1. Observe the farmer's singleton entering `LEAVING_POOL` state on-chain.
2. Monitor for any new `p2_singleton` reward coins farmed by the farmer's plots.
3. Submit an absorb spend (no signature required) before `relative_lock_height` blocks elapse.
4. Repeat indefinitely, resetting the countdown each time.

The farmer's singleton is permanently trapped in `LEAVING_POOL`. The farmer cannot transition to `SELF_POOLING` or `FARMING_TO_POOL` with a different pool. Their singleton-controlled asset is frozen in a state they cannot exit. This constitutes a **High** impact: unauthorized prevention of a protected singleton state transition, matching the allowed impact category "Bypass of pool authorization that enables unauthorized singleton mutation or protected state transitions." [5](#0-4) 

The `MINIMUM_RELATIVE_LOCK_HEIGHT` is 5 blocks and `MAXIMUM_RELATIVE_LOCK_HEIGHT` is 1000 blocks: [6](#0-5) 

With Chia's ~18-second block time, a pool with `relative_lock_height=32` needs only to absorb one reward every ~9.6 minutes to keep the farmer permanently trapped. Any actively farming user will produce rewards at a rate that makes this trivially exploitable.

### Likelihood Explanation

- The attacker is the pool operator, who has direct financial motivation (retaining farmers, blocking competitor migration).
- The attack requires no special access: absorb spends are permissionless and the required inputs (singleton coin, reward coin) are fully observable on-chain.
- The pool operator already runs infrastructure that monitors the blockchain for reward coins, making automation trivial.
- The farmer has no on-chain recourse once trapped.

### Recommendation

Replace `ASSERT_HEIGHT_RELATIVE` in the waiting room exit path with `ASSERT_HEIGHT_ABSOLUTE`, where the absolute height is computed as `entry_block + relative_lock_height` and is committed to when the singleton first enters the waiting room state. This mirrors the fix recommended in H-15: use an absolute reference point that cannot be reset by subsequent operations.

Alternatively, disallow absorb spends entirely while in the `LEAVING_POOL` state, forcing the farmer to exit before claiming any remaining rewards.

### Proof of Concept

```
1. Farmer at block N: submits travel spend, singleton enters LEAVING_POOL
   (relative_lock_height = 32, so exit allowed at block N+32)

2. Pool at block N+10: farmer's plot wins a block reward
   Pool submits create_absorb_spend(waiting_room_state, height=N+10)
   with G2Element() (no signature needed)
   → New singleton coin born at block N+10
   → Exit now requires block N+10+32 = N+42

3. Pool at block N+20: another reward arrives
   Pool submits absorb again
   → New singleton born at N+20
   → Exit now requires N+20+32 = N+52

4. Repeat indefinitely. Farmer never reaches the exit condition.
```

The permissionless absorb path is confirmed in `chia/pools/pool_puzzles.py`: [7](#0-6) 

And the waiting room puzzle is constructed with `relative_lock_height` as a curried parameter (not an absolute block): [8](#0-7)

### Citations

**File:** chia/pools/pool_puzzles.py (L51-64)
```python
def create_waiting_room_inner_puzzle(
    target_puzzle_hash: bytes32,
    relative_lock_height: uint32,
    owner_pubkey: G1Element,
    launcher_id: bytes32,
    genesis_challenge: bytes32,
    delay_time: uint64,
    delay_ph: bytes32,
) -> Program:
    pool_reward_prefix = bytes32(genesis_challenge[:16] + b"\x00" * 16)
    p2_singleton_puzzle_hash: bytes32 = launcher_id_to_p2_puzzle_hash(launcher_id, delay_time, delay_ph)
    return POOL_WAITING_ROOM_MOD.curry(
        target_puzzle_hash, p2_singleton_puzzle_hash, bytes(owner_pubkey), pool_reward_prefix, relative_lock_height
    )
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

**File:** chia/_tests/pools/test_pool_puzzles_lifecycle.py (L335-355)
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

        # LEAVE THE WAITING ROOM
        time = CoinTimestamp(20000000, 10000)
```

**File:** chia/pools/pool_wallet.py (L68-70)
```python
    MINIMUM_INITIAL_BALANCE: ClassVar[int] = 1
    MINIMUM_RELATIVE_LOCK_HEIGHT: ClassVar[int] = 5
    MAXIMUM_RELATIVE_LOCK_HEIGHT: ClassVar[int] = 1000
```

**File:** chia/pools/pool_wallet.py (L96-103)
```python

    The pool is also protected, by not allowing members to cheat by quickly leaving a pool,
    and claiming a block that was pledged to the pool.

    The pooling protocol and smart coin prevents a user from quickly leaving a pool
    by enforcing a wait time when leaving the pool. A minimum number of blocks must pass
    after the user declares that they are leaving the pool, and before they can start to
    self-claim rewards again.
```
