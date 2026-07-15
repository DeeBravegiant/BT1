### Title
Pool Can Indefinitely Reset Farmer's `LEAVING_POOL` Exit Timer via Unsigned Absorb Spends — (File: `chia/pools/pool_puzzles.py`)

---

### Summary

The Chia pool protocol's `LEAVING_POOL` (waiting room) state enforces a `relative_lock_height` block delay before a farmer can complete their exit. However, the `POOL_WAITINGROOM_INNERPUZ` also supports an absorb path (spend_type=0) that claims p2_singleton rewards **without requiring the owner's signature**. Each absorb spend creates a new singleton coin at the current block height, resetting the `ASSERT_HEIGHT_RELATIVE` timer at the CLVM level. A malicious pool, knowing the singleton puzzle and p2_singleton address, can continuously submit absorb spends to reset the timer, indefinitely preventing the farmer from leaving.

---

### Finding Description

When a farmer calls `pw_self_pool` or `pw_join_pool` from `FARMING_TO_POOL`, the singleton transitions to `LEAVING_POOL` state via `generate_travel_transactions`. The farmer must then wait `relative_lock_height` blocks before the second travel transaction can be submitted.

The wallet-side check in `new_peak()` computes:

```python
leave_height = tip_height + pool_wallet_info.current.relative_lock_height
if peak_height > leave_height + 2:
    # submit second travel transaction
``` [1](#0-0) 

Here `tip_height` is the height of the **most recent singleton spend** (from `get_tip()`), not the height at which the singleton first entered `LEAVING_POOL`. [2](#0-1) 

At the CLVM level, the `POOL_WAITINGROOM_INNERPUZ` enforces `ASSERT_HEIGHT_RELATIVE(relative_lock_height)` on the singleton coin. This condition is relative to the **creation height of the current singleton coin**. [3](#0-2) 

The waiting room puzzle supports two spend paths:
- **spend_type=1**: Exit the waiting room (requires owner signature, enforces `ASSERT_HEIGHT_RELATIVE`)
- **spend_type=0**: Absorb a p2_singleton reward (no owner signature required, creates a new singleton coin) [4](#0-3) 

The absorb path is explicitly confirmed to require no signature at fee=0: [5](#0-4) 

When the pool submits an absorb spend at block height H, a new singleton coin is created at height H. The `ASSERT_HEIGHT_RELATIVE(relative_lock_height)` on the new coin means the exit can only happen at height H + `relative_lock_height`. Both the wallet-side `leave_height` and the CLVM-enforced timelock are reset.

The pool knows all inputs needed to construct the absorb spend: the singleton puzzle (deterministic from `launcher_id` and inner puzzle), the p2_singleton puzzle, and the p2_singleton reward coin (visible on-chain). No farmer cooperation is needed. [6](#0-5) 

---

### Impact Explanation

**High** — The pool can permanently prevent a farmer from completing a `LEAVING_POOL` → `SELF_POOLING`/`FARMING_TO_POOL` transition. The farmer's plots continue farming to the malicious pool indefinitely. This is a bypass of a protected singleton state transition: the farmer's right to exit the pool is nullified without any on-chain authorization from the farmer. This matches: *"Bypass of wallet, daemon, keychain, RPC, pool, or Data Layer authorization that enables unauthorized signing, key use, coin control, payout redirection, singleton mutation, or protected state transitions"* and *"Permanent or long-lived inability for honest... farmers... to process valid... pool actions."*

---

### Likelihood Explanation

**High** — The pool is a full node operator with complete knowledge of the singleton puzzle and p2_singleton address. They can monitor the mempool and blockchain for p2_singleton reward coins and immediately submit absorb spends. Any actively farming user will periodically win blocks, producing new p2_singleton rewards the pool can absorb. The attack requires no special access, no leaked keys, and no cryptographic break.

---

### Recommendation

1. **Track the entry height of `LEAVING_POOL`**: Store the block height at which the singleton first entered `LEAVING_POOL` state and use that fixed height (not `tip_height`) for the `leave_height` calculation in `new_peak()`.
2. **Prevent absorbs in `LEAVING_POOL`**: Modify `POOL_WAITINGROOM_INNERPUZ` to disallow the absorb path once the exit has been initiated, or require the owner's signature for absorbs in the waiting room.
3. **Require owner signature for absorbs in waiting room**: Add `AGG_SIG_ME` to the absorb path of the waiting room puzzle, so the pool cannot unilaterally trigger absorbs.

---

### Proof of Concept

1. Farmer joins pool with `relative_lock_height = 100`. Pool is malicious.
2. Farmer calls `pw_self_pool` at block H. Singleton enters `LEAVING_POOL` at block H.
3. Pool monitors blockchain. At block H+99 (one block before exit is possible), a p2_singleton reward coin exists.
4. Pool constructs and submits an absorb spend (no signature required, fee=0) using `create_absorb_spend` with the waiting room singleton and the reward coin.
5. New singleton coin is created at block H+99. `ASSERT_HEIGHT_RELATIVE(100)` now requires block H+99+100 = H+199.
6. Wallet-side: `leave_height = (H+99) + 100 = H+199`. Farmer's `new_peak()` won't trigger until `peak_height > H+201`.
7. Pool repeats at block H+198. Timer resets to H+298.
8. Farmer can never complete the exit as long as p2_singleton rewards exist.

The absorb-while-in-waiting-room path is explicitly tested and confirmed valid in the production test suite: [7](#0-6)

### Citations

**File:** chia/pools/pool_wallet.py (L228-229)
```python
    async def get_tip(self) -> tuple[uint32, CoinSpend]:
        return (await self.wallet_state_manager.pool_store.get_spends_for_wallet(self.wallet_id))[-1]
```

**File:** chia/pools/pool_wallet.py (L782-783)
```python
        # If fee is 0, no signatures are required to absorb
        if fee > 0:
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

**File:** chia/pools/pool_puzzles.py (L252-307)
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
