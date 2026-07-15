### Title
Missing Zero Puzzle Hash Validation in Pool Wallet Join Flow Enables Permanent Reward Diversion - (File: `chia/pools/pool_wallet.py`, `chia/wallet/wallet_rpc_api.py`)

### Summary
The `_verify_pool_state` method in `PoolWallet` contains a dead-code null check for `target_puzzle_hash` (typed `bytes32`, which can never be `None`), while no check exists for the all-zeros value. The `pw_join_pool` RPC endpoint and the CLI's `join_pool` function accept `bytes32.zeros` as a valid `target_puzzlehash` without rejection. A malicious pool operator can return `"target_puzzle_hash": "0x0000...0000"` from their `/pool_info` endpoint, causing the user's pool singleton to be permanently reconfigured to direct all pool rewards to the zero address.

### Finding Description

`PoolWallet._verify_pool_state` is the sole validation gate for pool state transitions. Its check for `target_puzzle_hash` is:

```python
if state.target_puzzle_hash is None:
    return "Invalid puzzle_hash"
```

Since `target_puzzle_hash` is typed as `bytes32` (not `bytes32 | None`), this check is dead code — it can never be `True`. No check for `bytes32.zeros` exists anywhere in the validation chain. [1](#0-0) 

The `pw_join_pool` RPC handler in `WalletRpcApi` passes `request.target_puzzlehash` directly to `create_pool_state()` with no zero-value guard: [2](#0-1) 

The CLI's `join_pool` function in `plotnft_funcs.py` fetches `target_puzzle_hash` from the pool's HTTP `/pool_info` endpoint and passes it directly to `pw_join_pool` without any zero check: [3](#0-2) 

The `PoolState` dataclass confirms `target_puzzle_hash` is the final payout destination for pool rewards: [4](#0-3) 

The existing test suite confirms `bytes32.zeros` is accepted without error as `target_puzzlehash` in `pw_join_pool` calls — it is used as a routine test value: [5](#0-4) 

### Impact Explanation

`target_puzzle_hash` is the address to which the pool singleton pays out all block rewards. If set to `bytes32.zeros`, every reward coin created by the pool puzzle is sent to the zero address — permanently unspendable. This constitutes reward diversion affecting a pool wallet singleton-controlled asset. The singleton state is committed on-chain; the user cannot recover rewards already sent to the zero address. They can only leave the pool after the `relative_lock_height` elapses, losing all rewards accrued during that window.

### Likelihood Explanation

Any operator can run a pool (no privilege required). The CLI path (`chia plotnft join`) fetches `target_puzzle_hash` from the pool's HTTP response and passes it directly to the wallet RPC. The wallet performs no zero-value check at any layer. The user sees a `pprint` of the pool info but has no indication that a zero puzzle hash is invalid. The `_verify_pool_state` guard that was intended to catch bad puzzle hashes is dead code.

### Recommendation

1. In `PoolWallet._verify_pool_state`, replace the dead `is None` check with an actual zero-value check:
   ```python
   if state.target_puzzle_hash == bytes32.zeros:
       return "Invalid puzzle_hash: zero address"
   ```
2. In `pw_join_pool` (`wallet_rpc_api.py`), add an explicit guard before calling `create_pool_state`.
3. In the CLI's `join_pool` (`plotnft_funcs.py`), validate the pool-provided `target_puzzle_hash` is not all zeros before submitting the transaction.

### Proof of Concept

1. Operator runs a pool whose `/pool_info` endpoint returns `"target_puzzle_hash": "0x0000000000000000000000000000000000000000000000000000000000000000"`.
2. User runs: `chia plotnft join --pool-url https://malicious-pool.example.com`
3. `join_pool` in `plotnft_funcs.py` fetches pool info, constructs `PWJoinPool(target_puzzlehash=bytes32.zeros, ...)`, and calls `pw_join_pool`.
4. `pw_join_pool` calls `create_pool_state(FARMING_TO_POOL, bytes32.zeros, ...)` — no rejection.
5. `PoolWallet.join_pool` calls `_verify_initial_target_state` → `_verify_pool_state` → the `is None` check passes (dead code), `_verify_pooling_state` only checks `relative_lock_height` and `pool_url` — no rejection.
6. The singleton spend is submitted on-chain. The pool puzzle is now curried with `target_puzzle_hash = bytes32.zeros`.
7. All subsequent pool reward coins are created at the zero address and are permanently unspendable. [6](#0-5) [7](#0-6)

### Citations

**File:** chia/pools/pool_wallet.py (L145-186)
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

    @classmethod
    def _verify_pool_state(cls, state: PoolState) -> str | None:
        if state.target_puzzle_hash is None:
            return "Invalid puzzle_hash"

        if state.version > POOL_PROTOCOL_VERSION:
            return (
                f"Detected pool protocol version {state.version}, which is "
                f"newer than this wallet's version ({POOL_PROTOCOL_VERSION}). Please upgrade "
                f"to use this pooling wallet"
            )

        if state.state == PoolSingletonState.SELF_POOLING.value:
            return cls._verify_self_pooled(state)
        elif state.state in {PoolSingletonState.FARMING_TO_POOL.value, PoolSingletonState.LEAVING_POOL.value}:
            return cls._verify_pooling_state(state)
        else:
            return "Internal Error"

    @classmethod
    def _verify_initial_target_state(cls, initial_target_state: PoolState) -> None:
        err = cls._verify_pool_state(initial_target_state)
        if err:
            raise ValueError(f"Invalid internal Pool State: {err}: {initial_target_state}")
```

**File:** chia/wallet/wallet_rpc_api.py (L3221-3227)
```python
        new_target_state: PoolState = create_pool_state(
            FARMING_TO_POOL,
            request.target_puzzlehash,
            pool_wallet_info.current.owner_pubkey,
            request.pool_url,
            request.relative_lock_height,
        )
```

**File:** chia/cmds/plotnft_funcs.py (L381-392)
```python
    func = functools.partial(
        wallet_info.client.pw_join_pool,
        PWJoinPool(
            wallet_id=uint32(selected_wallet_id),
            target_puzzlehash=bytes32.from_hexstr(json_dict["target_puzzle_hash"]),
            pool_url=pool_url,
            relative_lock_height=json_dict["relative_lock_height"],
            fee=fee,
            push=True,
        ),
        DEFAULT_TX_CONFIG,
    )
```

**File:** chia/pools/pool_wallet_info.py (L53-56)
```python
    # `target_puzzle_hash`: A puzzle_hash we pay to
    # When self-farming, this is a main wallet address
    # When farming-to-pool, the pool sends this to the farmer during pool protocol setup
    target_puzzle_hash: bytes32  # TODO: rename target_puzzle_hash -> pay_to_address
```

**File:** chia/pools/pool_wallet_info.py (L117-130)
```python
def create_pool_state(
    state: PoolSingletonState,
    target_puzzle_hash: bytes32,
    owner_pubkey: G1Element,
    pool_url: str | None,
    relative_lock_height: uint32,
) -> PoolState:
    if state not in {s.value for s in PoolSingletonState}:
        raise AssertionError(f"state {state} is not a valid PoolSingletonState,")
    ps = PoolState(
        POOL_PROTOCOL_VERSION, uint8(state), target_puzzle_hash, owner_pubkey, pool_url, relative_lock_height
    )
    # TODO Move verify here
    return ps
```

**File:** chia/_tests/pools/test_pool_rpc.py (L942-1007)
```python
        pool_ph = bytes32.zeros

        assert wallet_node._wallet_state_manager is not None

        summaries_response = await client.get_wallets(GetWallets(type=uint16(WalletType.POOLING_WALLET)))
        assert len(summaries_response.wallets) == 0

        create_response_1 = await client.create_new_wallet(
            CreateNewWallet(
                wallet_type=CreateNewWalletType.POOL_WALLET,
                initial_target_state=NewPoolWalletInitialTargetState(
                    state="SELF_POOLING",
                ),
                mode=WalletCreationMode.NEW,
                fee=fee,
                push=True,
            ),
            DEFAULT_TX_CONFIG,
        )
        await full_node_api.wait_transaction_records_entered_mempool(records=create_response_1.transactions)
        await full_node_api.wait_for_wallet_synced(wallet_node=wallet_node, timeout=20)
        create_response_2 = await client.create_new_wallet(
            CreateNewWallet(
                wallet_type=CreateNewWalletType.POOL_WALLET,
                initial_target_state=NewPoolWalletInitialTargetState(
                    state="SELF_POOLING",
                ),
                mode=WalletCreationMode.NEW,
                fee=fee,
                push=True,
            ),
            DEFAULT_TX_CONFIG,
        )

        for r in create_response_1.transactions[0].removals:
            assert r not in create_response_2.transactions[0].removals

        await full_node_api.process_transaction_records(records=create_response_2.transactions)

        assert not full_node_api.txs_in_mempool(txs=create_response_1.transactions)
        await full_node_api.wait_for_wallet_synced(wallet_node=wallet_node, timeout=20)

        summaries_response = await client.get_wallets(GetWallets(type=uint16(WalletType.POOLING_WALLET)))
        assert len(summaries_response.wallets) == 2
        wallet_id: int = summaries_response.wallets[0].id
        wallet_id_2: int = summaries_response.wallets[1].id
        status: PoolWalletInfo = (await client.pw_status(PWStatus(wallet_id=uint32(wallet_id)))).state
        status_2: PoolWalletInfo = (await client.pw_status(PWStatus(wallet_id=uint32(wallet_id_2)))).state

        assert status.current.state == PoolSingletonState.SELF_POOLING.value
        assert status_2.current.state == PoolSingletonState.SELF_POOLING.value
        assert status.target is None
        assert status_2.target is None

        await full_node_api.wait_for_wallet_synced(wallet_node=wallet_node, timeout=20)
        join_pool = await client.pw_join_pool(
            PWJoinPool(
                wallet_id=uint32(wallet_id),
                target_puzzlehash=pool_ph,
                pool_url="https://pool.example.com",
                relative_lock_height=uint32(10),
                fee=uint64(fee),
                push=True,
            ),
            DEFAULT_TX_CONFIG,
        )
```
