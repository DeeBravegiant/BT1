### Title
Pool Can Trap Farmer in `LEAVING_POOL` State Indefinitely via Repeated Absorb Spends — (File: chia/pools/pool_wallet.py)

---

### Summary

A malicious pool can prevent a farmer from ever completing their exit from the pool by repeatedly performing absorb (reward-claim) spends while the farmer is in `LEAVING_POOL` state. Each absorb spend updates the singleton's `tip_height`, which continuously resets the `leave_height` countdown, keeping the farmer permanently trapped and their rewards flowing to the malicious pool.

---

### Finding Description

**Background — the two-step pool exit:**

Leaving a pool requires two on-chain state transitions:
1. `FARMING_TO_POOL` → `LEAVING_POOL` (farmer initiates)
2. `LEAVING_POOL` → `SELF_POOLING` (or another pool), only after `relative_lock_height` blocks have elapsed since the most recent singleton spend

The second transition is gated in `new_peak()`:

```python
# chia/pools/pool_wallet.py  lines 819-826
if (
    self.target_state.state in {FARMING_TO_POOL.value, SELF_POOLING.value}
    and pool_wallet_info.current.state == LEAVING_POOL.value
):
    leave_height = tip_height + pool_wallet_info.current.relative_lock_height
    # Add some buffer (+2) to reduce chances of a reorg
    if peak_height > leave_height + 2:
        ...
        await self.generate_travel_transactions(...)
```

`tip_height` is the block height of the **most recent singleton spend** (from `get_tip()`), regardless of whether that spend was a state-transition or an absorb.

**The absorb spend path is valid in `LEAVING_POOL` state:**

`create_absorb_spend` in `pool_puzzles.py` explicitly handles the waiting-room (LEAVING_POOL) inner puzzle:

```python
# chia/pools/pool_puzzles.py  lines 268-270
elif is_pool_waitingroom_inner_puzzle(inner_puzzle):
    # inner sol is (spend_type, destination_puzhash, pool_reward_amount, pool_reward_height, extra_data)
    inner_sol = Program.to([0, reward_amount, height])
```

This spend path requires **no owner signature** — it is permissionless. The pool already knows the inner puzzle hash (revealed during pool setup) and can construct and submit absorb spends independently.

**How the attack works:**

Every absorb spend creates a new singleton coin at the current block height and is recorded in the pool store via `apply_state_transition`:

```python
# chia/pools/pool_wallet.py  line 286
await self.wallet_state_manager.pool_store.add_spend(self.wallet_id, new_state, block_height)
```

This updates `tip_height` to the current block. Since `leave_height = tip_height + relative_lock_height`, each absorb spend pushes the exit deadline forward by the full `relative_lock_height`. A malicious pool that submits an absorb spend every `relative_lock_height − 1` blocks keeps the farmer permanently in `LEAVING_POOL` state.

The pool has farming rewards arriving continuously (the `p2_singleton` coins), giving it an unlimited supply of absorb spends to perform.

**Missing guard:**

`new_peak()` uses the height of the most recent singleton spend without distinguishing between state-transition spends and absorb spends. There is no fixed anchor recording when the singleton *entered* `LEAVING_POOL` state.

---

### Impact Explanation

The farmer is permanently trapped in `LEAVING_POOL` state. They cannot redirect farming rewards to themselves or join another pool. Their block rewards continue to flow to the malicious pool's `target_puzzle_hash` indefinitely. This is a **High** impact: permanent inability for an honest farmer to complete a pool state transition, with direct financial harm (ongoing reward diversion).

---

### Likelihood Explanation

Any pool the farmer tries to leave can execute this attack. The pool has a direct financial incentive (retaining the farmer's rewards). The attack requires no special privileges, no leaked keys, and no admin compromise — only the ability to submit valid absorb spends, which the pool can do permissionlessly using farming rewards it already receives.

---

### Recommendation

Record the block height at which the singleton *entered* `LEAVING_POOL` state (i.e., the height of the state-transition spend that set `state = LEAVING_POOL`) and use that fixed height — not `tip_height` — as the reference point for computing `leave_height`. Absorb spends should not reset the countdown. Alternatively, the waiting-room CLVM puzzle (`pool_waitingroom_innerpuz`) could embed the entry height as a constant and enforce the timelock relative to it, making the countdown immune to absorb-spend manipulation.

---

### Proof of Concept

1. Farmer joins a malicious pool with `relative_lock_height = 1000` (within the allowed maximum).
2. Farmer calls `pw_self_pool()` at block H; singleton enters `LEAVING_POOL`.
3. Pool submits an absorb spend at block H + 998 using a farming reward coin.
4. `tip_height` is now H + 998; `leave_height = H + 998 + 1000 = H + 1998`.
5. Pool repeats step 3 every ~998 blocks.
6. `leave_height` is always ~1000 blocks in the future; `new_peak()` never fires the second transition.
7. Farmer is permanently trapped; all farming rewards continue to flow to the pool.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** chia/pools/pool_wallet.py (L284-287)
```python
            return False

        await self.wallet_state_manager.pool_store.add_spend(self.wallet_id, new_state, block_height)
        tip_spend = (await self.get_tip())[1]
```

**File:** chia/pools/pool_wallet.py (L807-812)
```python
    async def new_peak(self, peak_height: uint32) -> None:
        # This gets called from the WalletStateManager whenever there is a new peak

        pool_wallet_info: PoolWalletInfo = await self.get_current_state()
        tip_height, tip_spend = await self.get_tip()

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

**File:** chia/pools/pool_puzzles.py (L268-271)
```python
    elif is_pool_waitingroom_inner_puzzle(inner_puzzle):
        # inner sol is (spend_type, destination_puzhash, pool_reward_amount, pool_reward_height, extra_data)
        inner_sol = Program.to([0, reward_amount, height])
    else:
```

**File:** chia/pools/pool_wallet_info.py (L15-32)
```python
class PoolSingletonState(IntEnum):
    """
    From the user's point of view, a pool group can be in these states:
    `SELF_POOLING`: The singleton exists on the blockchain, and we are farming
        block rewards to a wallet address controlled by the user

    `LEAVING_POOL`: The singleton exists, and we have entered the "escaping" state, which
        means we are waiting for a number of blocks = `relative_lock_height` to pass, so we can leave.

    `FARMING_TO_POOL`: The singleton exists, and it is assigned to a pool.

    `CLAIMING_SELF_POOLED_REWARDS`: We have submitted a transaction to sweep our
        self-pooled funds.
    """

    SELF_POOLING = 1
    LEAVING_POOL = 2
    FARMING_TO_POOL = 3
```
