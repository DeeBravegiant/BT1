Let me look at a few more critical details before rendering a verdict.

Now I have a complete picture of the code path. Let me render the verdict.

---

### Title
Unauthenticated Peer Can Corrupt Pool Wallet Membership State via Fabricated `CoinSpend` Solution — (`chia/wallet/util/wallet_sync_utils.py`, `chia/pools/pool_wallet.py`)

### Summary

An unprivileged remote full-node peer can corrupt a victim wallet's pool membership state by returning a fabricated solution (with a real puzzle) in response to a `request_puzzle_solution` call. The wallet validates the puzzle hash but never validates the solution against the blockchain. `apply_state_transition` stores the fabricated `CoinSpend` and `update_pool_config` writes the attacker-controlled `pool_state` to the pool config, causing the farmer to report to the wrong pool and lose rewards.

### Finding Description

**Step 1 — Entrypoint: `_add_coin_states` in `wallet_state_manager.py`**

When a `coin_state_update` arrives for a `POOLING_WALLET` singleton with `spent_height` set, the wallet calls:

```python
cs: CoinSpend = await fetch_coin_spend_for_coin_state(curr_coin_state, peer)
```

where `peer` is the same peer that sent the coin state update. [1](#0-0) 

**Step 2 — Missing solution validation in `fetch_coin_spend`**

`fetch_coin_spend` validates only the puzzle hash and coin name. The solution is accepted verbatim from the peer:

```python
if solution_response.response.puzzle.get_tree_hash() != coin.puzzle_hash:
    raise PeerRequestException(...)
if solution_response.response.coin_name != coin_id:
    raise PeerRequestException(...)
# solution is NOT validated
return make_spend(coin, solution_response.response.puzzle, solution_response.response.solution)
``` [2](#0-1) 

The attacker returns the correct puzzle (matching `coin.puzzle_hash`, which is validated by Merkle proof) but a fabricated solution encoding a malicious `pool_state`.

**Step 3 — `apply_state_transition` stores without cryptographic validation**

`apply_state_transition` only checks that the coin ID matches the current tip. There is no signature check, no CLVM execution of the puzzle against the solution, and no comparison against the actual on-chain spend:

```python
if spent_coin_name != new_state.coin.name():
    ...
    return False
await self.wallet_state_manager.pool_store.add_spend(self.wallet_id, new_state, block_height)
``` [3](#0-2) 

The `pool_store.add_spend` chain-continuity check (`spend.coin.parent_coin_info != prev.coin.name()`) passes because the coin itself is real. [4](#0-3) 

**Step 4 — `solution_to_pool_state` parses attacker-controlled bytes**

`solution_to_pool_state` deserializes the pool state purely from the solution bytes with no cryptographic validation:

```python
inner_solution: Program = full_solution.rest().rest().first()
...
extra_data = inner_solution.first()
return pool_state_from_extra_data(extra_data)
``` [5](#0-4) 

`pool_state_from_extra_data` simply calls `PoolState.from_bytes(state_bytes)` — no owner key verification. [6](#0-5) 

**Step 5 — `update_pool_config` writes attacker-controlled values**

After storing the fabricated spend, `apply_state_transition` calls `update_pool_config`, which writes the attacker's `pool_url` and `target_puzzle_hash` to the pool config file:

```python
pool_config.pool_url = current_state.current.pool_url if current_state.current.pool_url else ""
pool_config.target_puzzle_hash = current_state.current.target_puzzle_hash
pool_config.owner_public_key = current_state.current.owner_pubkey
``` [7](#0-6) 

### Impact Explanation

The pool config is the authoritative source the farmer uses to know which pool to report to (`pool_url`) and what the farmer's payout address is (`target_puzzle_hash`). Corrupting it causes:

1. **Farmer reports to attacker's pool** — the attacker's pool collects proof-of-space submissions and can simply withhold payment, causing the farmer to lose all pool rewards.
2. **Pool membership state permanently corrupted** — `get_current_state` reads from the pool store and returns the attacker's `pool_state`, so all subsequent wallet logic (including `join_pool`, `self_pool`, `generate_travel_transactions`) operates on attacker-controlled state.
3. **`owner_public_key` in config overwritten** — the farmer's identity with the pool is replaced by the attacker's pubkey.

Note: on-chain singleton behavior is unaffected (the CLVM puzzle still enforces the original `target_puzzle_hash`), so block rewards are not directly redirected on-chain. The impact is loss of pool rewards and persistent local state corruption.

### Likelihood Explanation

- The attacker only needs to be a peer the wallet connects to (any untrusted full node).
- The trigger condition (a pool wallet singleton being spent) occurs naturally during absorb spends, pool changes, or any singleton state transition.
- No key material is required; the attacker only needs to intercept the `request_puzzle_solution` response.
- The wallet's `validate_received_state_from_peer` validates coin states via Merkle proofs but provides no protection for the solution content. [8](#0-7) 

### Recommendation

In `fetch_coin_spend`, after obtaining the puzzle and solution from the peer, validate the solution against the blockchain by either:
1. Fetching the block generator for `height` and running `get_puzzle_and_solution_for_coin` locally (as the full node does in `request_puzzle_solution`), then comparing the result.
2. Alternatively, in `apply_state_transition`, re-derive the expected `pool_state` from the known singleton puzzle (which is deterministically computable from the launcher ID and owner pubkey stored locally) and reject any `CoinSpend` whose solution produces a `pool_state` inconsistent with the known owner pubkey.

### Proof of Concept

```python
# Mock peer returns correct puzzle but fabricated solution
malicious_pool_state = PoolState(
    version=1, state=FARMING_TO_POOL.value,
    target_puzzle_hash=attacker_puzzle_hash,
    owner_pubkey=attacker_pubkey,
    pool_url="https://attacker.pool",
    relative_lock_height=uint32(32),
)
fabricated_solution = craft_solution_encoding(malicious_pool_state)

# peer.call_api(request_puzzle_solution) returns:
# RespondPuzzleSolution(PuzzleSolutionResponse(
#     coin_name=real_singleton_coin_id,
#     height=real_spent_height,
#     puzzle=real_singleton_puzzle,   # hash matches coin.puzzle_hash ✓
#     solution=fabricated_solution,   # not validated ✗
# ))

# After processing:
state = await pool_wallet.get_current_state()
assert state.current.pool_url == "https://attacker.pool"  # passes
assert state.current.target_puzzle_hash == attacker_puzzle_hash  # passes
```

### Citations

**File:** chia/wallet/wallet_state_manager.py (L2089-2094)
```python
                                while curr_coin_state.spent_height is not None:
                                    cs: CoinSpend = await fetch_coin_spend_for_coin_state(curr_coin_state, peer)
                                    async with self.new_action_scope(self.tx_config, push=True) as action_scope:
                                        success = await singleton_wallet.apply_state_transition(
                                            cs, uint32(curr_coin_state.spent_height), action_scope
                                        )
```

**File:** chia/wallet/util/wallet_sync_utils.py (L336-352)
```python
async def fetch_coin_spend(height: uint32, coin: Coin, peer: WSChiaConnection) -> CoinSpend:
    solution_response = await peer.call_api(
        FullNodeAPI.request_puzzle_solution, RequestPuzzleSolution(coin.name(), height)
    )
    if solution_response is None or not isinstance(solution_response, RespondPuzzleSolution):
        raise PeerRequestException(f"Was not able to obtain solution {solution_response}")
    coin_id = coin.name()
    if solution_response.response.puzzle.get_tree_hash() != coin.puzzle_hash:
        raise PeerRequestException(f"Peer returned wrong puzzle hash for coin {coin_id}")
    if solution_response.response.coin_name != coin_id:
        raise PeerRequestException(f"Peer returned wrong coin name in puzzle solution for coin {coin_id}")

    return make_spend(
        coin,
        solution_response.response.puzzle,
        solution_response.response.solution,
    )
```

**File:** chia/pools/pool_wallet.py (L254-259)
```python
            pool_config.launcher_id = current_state.launcher_id
            pool_config.pool_url = current_state.current.pool_url if current_state.current.pool_url else ""
            pool_config.payout_instructions = payout_instructions
            pool_config.target_puzzle_hash = current_state.current.target_puzzle_hash
            pool_config.p2_singleton_puzzle_hash = current_state.p2_singleton_puzzle_hash
            pool_config.owner_public_key = current_state.current.owner_pubkey
```

**File:** chia/pools/pool_wallet.py (L276-286)
```python
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
```

**File:** chia/wallet/wallet_pool_store.py (L74-76)
```python
                prev = CoinSpend.from_bytes(row[2])
                if spend.coin.parent_coin_info != prev.coin.name():
                    raise ValueError("New spend does not extend")
```

**File:** chia/pools/pool_puzzles.py (L384-396)
```python
def pool_state_from_extra_data(extra_data: Program) -> PoolState | None:
    state_bytes: bytes | None = None
    try:
        for key, value in extra_data.as_python():
            if key == b"p":
                state_bytes = value
                break
        if state_bytes is None:
            return None
        return PoolState.from_bytes(state_bytes)
    except TypeError as e:
        log.error(f"Unexpected return from PoolWallet Smart Contract code {e}")
        return None
```

**File:** chia/pools/pool_puzzles.py (L409-427)
```python
    inner_solution: Program = full_solution.rest().rest().first()

    # Spend which is not absorb, and is not the launcher
    num_args = len(inner_solution.as_python())
    assert num_args in {2, 3}

    if num_args == 2:
        # pool member
        if inner_solution.rest().first().as_int() != 0:
            return None

        # This is referred to as p1 in the chialisp code
        # spend_type is absorbing money if p1 is a cons box, spend_type is escape if p1 is an atom
        # TODO: The comment above, and in the CLVM, seems wrong
        extra_data = inner_solution.first()
        if isinstance(extra_data.as_python(), bytes):
            # Absorbing
            return None
        return pool_state_from_extra_data(extra_data)
```

**File:** chia/wallet/wallet_node.py (L1564-1571)
```python
        validate_additions_result = await request_and_validate_additions(
            peer,
            peer_request_cache,
            state_block.height,
            state_block.header_hash,
            coin_state.coin.puzzle_hash,
            state_block.foliage_transaction_block.additions_root,
        )
```
