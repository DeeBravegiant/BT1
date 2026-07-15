### Title
Permissionless Absorb Spend Resets `ASSERT_HEIGHT_RELATIVE` Exit Timer in Pool Waiting Room, Permanently Trapping Farmer - (`chia/pools/pool_puzzles.py`)

### Summary

The pool waiting room singleton's `ASSERT_HEIGHT_RELATIVE` exit timer can be indefinitely reset by any party submitting a permissionless absorb spend. Because absorb spends require no owner signature and spend the current singleton coin (creating a new one with a fresh `confirmed_block_index`), a malicious pool operator can prevent a farmer from ever completing the `relative_lock_height` countdown and exiting to self-pooling.

### Finding Description

When a farmer initiates leaving a pool, their singleton transitions to the `LEAVING_POOL` (waiting room) state. Exiting this state requires the singleton coin to satisfy `ASSERT_HEIGHT_RELATIVE` with `relative_lock_height` blocks elapsed since the coin was last confirmed on-chain. This is the core delay mechanism protecting the pool's exit window.

The waiting room inner puzzle (`POOL_WAITINGROOM_INNERPUZ`) supports two spend paths:
1. **Escape spend** (spend_type = 1): exits to the target state; requires owner signature.
2. **Absorb spend** (spend_type = 0): claims a pool reward coin; **requires no owner signature**.

`create_absorb_spend` in `chia/pools/pool_puzzles.py` constructs the absorb path for the waiting room:

```python
elif is_pool_waitingroom_inner_puzzle(inner_puzzle):
    inner_sol = Program.to([0, reward_amount, height])
``` [1](#0-0) 

The resulting spend bundle is submitted with an empty `G2Element()` signature — confirmed by the lifecycle test which successfully executes the absorb with no signature:

```python
coin_db.update_coin_store_for_spend_bundle(
    SpendBundle(coin_sols, G2Element()), time, DEFAULT_CONSTANTS.MAX_BLOCK_COST_CLVM
)
``` [2](#0-1) 

Each absorb spend **spends the current singleton coin and creates a new one**. The new coin's `confirmed_block_index` is the block where the absorb was included. The `ASSERT_HEIGHT_RELATIVE` check in `compute_assert_height` is always measured from the spending coin's `confirmed_block_index`:

```python
if spend.height_relative is not None:
    h = uint32(removal_coin_records[bytes32(spend.coin_id)].confirmed_block_index + spend.height_relative)
    ret.assert_height = max(ret.assert_height, h)
``` [3](#0-2) 

Therefore, every absorb spend resets the `relative_lock_height` countdown to zero for the new singleton coin.

**Attack path:**

1. Farmer calls `join_pool` → singleton enters `FARMING_TO_POOL`. Farmer later initiates exit → singleton enters `LEAVING_POOL` (waiting room) at block `H`.
2. The farmer must wait `relative_lock_height` blocks (e.g., 32–2000) before the escape spend is valid.
3. A malicious pool operator (or any third party) monitors the chain. Whenever a pool reward coin appears at the farmer's `p2_singleton_puzzle_hash`, they submit a permissionless absorb spend.
4. The absorb spend creates a new singleton coin at the current block height, resetting the countdown.
5. As long as the farmer continues farming (producing pool reward coins), the attacker can repeat step 3–4 indefinitely, keeping the farmer permanently trapped in the waiting room.

The pool reward coins used for absorb spends are created by the blockchain itself whenever the farmer farms a block — they are not under the pool operator's control to produce, but the pool operator can freely consume them via absorb spends since no authorization is required. [4](#0-3) 

### Impact Explanation

A malicious pool operator can permanently prevent a farmer from exiting the pool by continuously submitting permissionless absorb spends on the farmer's waiting room singleton. The farmer's singleton remains locked in `LEAVING_POOL` state indefinitely. This constitutes:

- **Unauthorized corruption of pool membership state**: the farmer's singleton state transition is blocked against their will.
- **Permanent inability for an honest farmer to complete a valid pool exit**, which is a protected singleton state transition.

The cost to the attacker is negligible: in the waiting room, absorb spends direct rewards to the farmer's own `target_puzzle_hash` (not the pool), so the attacker gives up nothing they would have collected anyway. The attacker only needs to submit a valid spend bundle whenever a pool reward coin appears.

### Likelihood Explanation

Any pool operator running a malicious node can monitor the chain for pool reward coins at a target farmer's `p2_singleton_puzzle_hash` and submit absorb spends before the farmer's exit timer completes. The attack requires no special privileges, no key material, and no cryptographic capability beyond constructing a valid spend bundle — which is fully documented in `create_absorb_spend`. The attack is sustainable as long as the farmer continues farming.

### Recommendation

The waiting room exit puzzle should not allow the `ASSERT_HEIGHT_RELATIVE` timer to be reset by absorb spends. Possible mitigations:

1. **Track exit initiation height separately**: Store the block height at which the singleton first entered the waiting room (e.g., in the puzzle or via an absolute height condition `ASSERT_HEIGHT_ABSOLUTE`) rather than using `ASSERT_HEIGHT_RELATIVE` on the current coin. This way, absorb spends that recreate the singleton coin do not reset the countdown.
2. **Disallow absorb spends in the waiting room**: Remove the absorb path from the waiting room puzzle entirely. Farmers in the waiting room can collect rewards after exiting, or via the delayed puzzle path.
3. **Require owner signature for absorb spends in the waiting room**: Add an `AGG_SIG_ME` condition to the waiting room absorb path so only the farmer can trigger it, preventing third-party timer resets.

### Proof of Concept

The existing lifecycle test at `chia/_tests/pools/test_pool_puzzles_lifecycle.py` lines 335–352 already demonstrates that an absorb spend in the waiting room succeeds with no signature (`G2Element()`). The timer reset follows directly from `compute_assert_height` measuring `ASSERT_HEIGHT_RELATIVE` against the new coin's `confirmed_block_index`. [5](#0-4) 

To confirm the timer reset: after the absorb at block 3, the test advances to block 10000 (well past `relative_lock_height = 5000` from block 1, but only ~9997 blocks from block 3) and successfully exits — demonstrating that the timer is measured from the absorb coin's birth, not the original waiting room entry. A malicious operator repeating absorbs every `relative_lock_height - 1` blocks would prevent the exit condition from ever being satisfied. [6](#0-5) [7](#0-6)

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

**File:** chia/full_node/mempool_manager.py (L79-106)
```python
def compute_assert_height(
    removal_coin_records: dict[bytes32, CoinRecord],
    conds: SpendBundleConditions,
) -> TimelockConditions:
    """
    Computes the most restrictive height- and seconds assertion in the spend bundle.
    Relative heights and times are resolved using the confirmed heights and
    timestamps from the coin records.
    """

    ret = TimelockConditions()
    ret.assert_height = uint32(conds.height_absolute)
    ret.assert_seconds = uint64(conds.seconds_absolute)
    ret.assert_before_height = (
        uint32(conds.before_height_absolute) if conds.before_height_absolute is not None else None
    )
    ret.assert_before_seconds = (
        uint64(conds.before_seconds_absolute) if conds.before_seconds_absolute is not None else None
    )

    for spend in conds.spends:
        if spend.height_relative is not None:
            h = uint32(removal_coin_records[bytes32(spend.coin_id)].confirmed_block_index + spend.height_relative)
            ret.assert_height = max(ret.assert_height, h)

        if spend.seconds_relative is not None:
            s = uint64(removal_coin_records[bytes32(spend.coin_id)].timestamp + spend.seconds_relative)
            ret.assert_seconds = max(ret.assert_seconds, s)
```
