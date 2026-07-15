### Title
Unconstrained `p2_singleton_delay_time` Allows Immediate Pool Reward Diversion via Delayed Spend Path — (`File: chia/pools/pool_wallet.py`)

### Summary
The `create_new_pool_wallet_transaction()` function accepts a caller-supplied `p2_singleton_delay_time` with no minimum value enforcement. When set to `0` or `1`, the `SECONDS_DELAY` parameter baked into the `p2_singleton_or_delayed_puzhash` on-chain puzzle becomes trivially satisfiable, allowing the farmer to immediately redirect block rewards away from the pool's claim path to their own wallet — permanently, for the lifetime of that singleton.

### Finding Description

The `p2_singleton_delay_time` parameter flows from the wallet RPC all the way into the on-chain puzzle without any minimum value check:

**Step 1 — RPC schema accepts any `uint64`:**

In `chia/wallet/wallet_request_types.py`, `CreateNewWallet` declares `p2_singleton_delay_time: uint64 | None = None`. The `__post_init__` method only checks that this field is absent for non-pool wallet types; it imposes no minimum value when it is present. [1](#0-0) [2](#0-1) 

**Step 2 — RPC handler passes the value through unchanged:**

`WalletRpcApi.create_new_wallet()` forwards `request.p2_singleton_delay_time` directly to `PoolWallet.create_new_pool_wallet_transaction()`. [3](#0-2) 

**Step 3 — Wallet function only substitutes `None`, never validates the value:**

`create_new_pool_wallet_transaction()` replaces `None` with the safe default of `604800` (7 days), but if the caller explicitly passes `uint64(0)` or `uint64(1)`, that value is used as-is and passed into `generate_launcher_spend()`. [4](#0-3) [5](#0-4) 

**Step 4 — The value is permanently baked into the on-chain puzzle:**

`generate_launcher_spend()` calls `launcher_id_to_p2_puzzle_hash(launcher_id, delay_time, delay_ph)`, which curries `SECONDS_DELAY` into the `P2_SINGLETON_OR_DELAYED_PUZHASH` CLVM puzzle. This puzzle hash is committed to the blockchain in the launcher spend and cannot be changed. [6](#0-5) [7](#0-6) 

**Step 5 — The delayed spend path becomes immediately exploitable:**

The `p2_singleton_or_delayed_puzhash` puzzle has two spend paths: (a) the singleton claims the reward coin, or (b) after `SECONDS_DELAY` seconds, anyone can forward the coin to `delayed_puzzle_hash`. With `SECONDS_DELAY = 0`, path (b) is immediately available. The farmer can call `spend_to_delayed_puzzle()` to redirect any reward coin sitting at `p2_singleton_puzzle_hash` to `delayed_puzzle_hash` (their own wallet) before the pool can claim it via path (a). [8](#0-7) 

The existing `_verify_pooling_state()` validates `relative_lock_height` but has no corresponding check for `delay_time`. [9](#0-8) 

### Impact Explanation

A farmer creates a pool singleton with `p2_singleton_delay_time=0`, joins a pool (the pool protocol does not mandate that pools validate `delay_time` — it is not part of the `PoolState` streamable), and farms blocks normally. Every time a block reward lands at the `p2_singleton_puzzle_hash`, the farmer immediately spends it via the delayed path to their own wallet before the pool can absorb it. The pool permanently loses all rewards for that singleton. The singleton itself remains valid and continues to farm, so the attack is ongoing and undetectable until the pool notices zero absorb transactions. The singleton cannot be re-created with a corrected `delay_time`; the launcher spend is immutable.

### Likelihood Explanation

The attack requires only a local wallet RPC call with a non-default parameter value — no special privileges, no leaked keys, no cryptographic break. The parameter is documented as optional with a safe default, making it easy to overlook. A malicious farmer who wants to cheat a pool while appearing legitimate has a clear, low-cost path to do so.

### Recommendation

Add a minimum value check for `p2_singleton_delay_time` in `create_new_pool_wallet_transaction()` before it is used:

```python
MIN_P2_SINGLETON_DELAY_TIME = uint64(3600)  # e.g., 1 hour minimum

if p2_singleton_delay_time < MIN_P2_SINGLETON_DELAY_TIME:
    raise ValueError(
        f"p2_singleton_delay_time ({p2_singleton_delay_time}) is below the "
        f"minimum allowed value ({MIN_P2_SINGLETON_DELAY_TIME})"
    )
```

This check should be placed immediately after the `None`-substitution at line 415 of `chia/pools/pool_wallet.py`, and a corresponding check should be added in `CreateNewWallet.__post_init__()` in `chia/wallet/wallet_request_types.py` to reject invalid values at the RPC boundary. [4](#0-3) [2](#0-1) 

### Proof of Concept

```python
# Attacker calls the wallet RPC with delay_time=0
create_response = await client.create_new_wallet(
    CreateNewWallet(
        wallet_type=CreateNewWalletType.POOL_WALLET,
        initial_target_state=NewPoolWalletInitialTargetState(
            target_puzzle_hash=pool_ph,
            state="FARMING_TO_POOL",
            pool_url="https://legitimate-pool.example.com",
            relative_lock_height=uint32(32),
        ),
        mode=WalletCreationMode.NEW,
        p2_singleton_delay_time=uint64(0),   # <-- no validation blocks this
        p2_singleton_delayed_ph=attacker_wallet_ph,
        push=True,
    ),
    DEFAULT_TX_CONFIG,
)
# Singleton is created on-chain with SECONDS_DELAY=0 baked into p2_singleton puzzle.
# Pool accepts the farmer (relative_lock_height is valid; delay_time is not checked).
# Each time a reward coin lands at p2_singleton_puzzle_hash:
reward_divert_spend = spend_to_delayed_puzzle(
    reward_coin,
    reward_coin.amount,
    launcher_id,
    uint64(0),          # SECONDS_DELAY=0 → ASSERT_SECONDS_RELATIVE 0 always passes
    attacker_wallet_ph,
)
# This spend is valid immediately; pool's absorb spend races and loses.
# Attacker receives all pool rewards; pool receives nothing.
```

### Citations

**File:** chia/wallet/wallet_request_types.py (L2383-2384)
```python
    p2_singleton_delayed_ph: bytes32 | None = None
    p2_singleton_delay_time: uint64 | None = None
```

**File:** chia/wallet/wallet_request_types.py (L2455-2464)
```python
        if self.wallet_type == CreateNewWalletType.POOL_WALLET:
            if self.initial_target_state is None:
                raise ValueError('"initial_target_state" is required for new pool wallets')
        else:
            if self.initial_target_state is not None:
                raise ValueError('"initial_target_state" is only a valid argument for pool wallets')
            if self.p2_singleton_delayed_ph is not None:
                raise ValueError('"p2_singleton_delayed_ph" is only a valid argument for pool wallets')
            if self.p2_singleton_delay_time is not None:
                raise ValueError('"p2_singleton_delay_time" is only a valid argument for pool wallets')
```

**File:** chia/wallet/wallet_rpc_api.py (L1310-1319)
```python
                    p2_singleton_puzzle_hash, launcher_id = await PoolWallet.create_new_pool_wallet_transaction(
                        wallet_state_manager,
                        main_wallet,
                        initial_target_state,
                        action_scope,
                        request.fee,
                        request.p2_singleton_delay_time,
                        request.p2_singleton_delayed_ph,
                        extra_conditions=extra_conditions,
                    )
```

**File:** chia/pools/pool_wallet.py (L145-161)
```python
    @classmethod
    def _verify_pooling_state(cls, state: PoolState) -> str | None:
        err = ""
        if state.relative_lock_height < cls.MINIMUM_RELATIVE_LOCK_HEIGHT:
            err += (
                f" Pool relative_lock_height ({state.relative_lock_height})"
                f"is less than recommended minimum ({cls.MINIMUM_RELATIVE_LOCK_HEIGHT})"
            )
        elif state.relative_lock_height > cls.MAXIMUM_RELATIVE_LOCK_HEIGHT:
            err += (
                f" Pool relative_lock_height ({state.relative_lock_height})"
                f"is greater than recommended maximum ({cls.MAXIMUM_RELATIVE_LOCK_HEIGHT})"
            )

        if state.pool_url in {None, ""}:
            err += " Empty pool url in pooling state"
        return err
```

**File:** chia/pools/pool_wallet.py (L414-415)
```python
        if p2_singleton_delay_time is None:
            p2_singleton_delay_time = uint64(604800)
```

**File:** chia/pools/pool_wallet.py (L427-437)
```python
        _singleton_puzzle_hash, launcher_coin_id = await PoolWallet.generate_launcher_spend(
            standard_wallet,
            uint64(1),
            fee,
            initial_target_state,
            wallet_state_manager.constants.GENESIS_CHALLENGE,
            p2_singleton_delay_time,
            p2_singleton_delayed_ph,
            action_scope,
            extra_conditions=extra_conditions,
        )
```

**File:** chia/pools/pool_puzzles.py (L97-100)
```python
    # curry params are SINGLETON_MOD_HASH LAUNCHER_ID LAUNCHER_PUZZLE_HASH SECONDS_DELAY DELAYED_PUZZLE_HASH
    return P2_SINGLETON_MOD.curry(
        singleton_mod_hash, launcher_id, SINGLETON_LAUNCHER_HASH, seconds_delay, delayed_puzzle_hash
    )
```

**File:** chia/pools/pool_puzzles.py (L120-121)
```python
def launcher_id_to_p2_puzzle_hash(launcher_id: bytes32, seconds_delay: uint64, delayed_puzzle_hash: bytes32) -> bytes32:
    return create_p2_singleton_puzzle_hash(SINGLETON_MOD_HASH, launcher_id, seconds_delay, delayed_puzzle_hash)
```

**File:** chia/wallet/puzzles/singleton_top_layer_v1_1.py (L349-361)
```python
def spend_to_delayed_puzzle(
    p2_singleton_coin: Coin,
    output_amount: uint64,
    launcher_id: bytes32,
    delay_time: uint64,
    delay_ph: bytes32,
) -> CoinSpend:
    claim_coinsol = make_spend(
        p2_singleton_coin,
        pay_to_singleton_or_delay_puzzle(launcher_id, delay_time, delay_ph),
        solution_for_p2_delayed_puzzle(output_amount),
    )
    return claim_coinsol
```
