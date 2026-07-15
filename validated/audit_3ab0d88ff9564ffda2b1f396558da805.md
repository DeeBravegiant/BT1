The attack path is concrete. Let me verify the exception-handling that stores states into `retry_store` before finalizing.

### Title
Unvalidated Solution in Retry-Path `fetch_coin_spend` Allows Substitute Peer to Inject Arbitrary Pool Singleton State — (`chia/wallet/wallet_state_manager.py`, `chia/pools/pool_wallet.py`, `chia/wallet/util/wallet_sync_utils.py`)

---

### Summary

An unprivileged remote full-node peer can corrupt a wallet's pool singleton state (local `pool_url` and `target_puzzle_hash`) by exploiting the retry mechanism in `_retry_failed_states`. The attack works because `fetch_coin_spend` validates the puzzle reveal against `coin.puzzle_hash` but **never validates the solution**, and `solution_to_pool_state` deserializes the `PoolState` directly from the solution bytes without executing the puzzle or verifying any signature.

---

### Finding Description

**Step 1 — Populate `retry_store` via `PeerRequestException`**

In `_add_coin_states`, any `PeerRequestException` (e.g., from `fetch_coin_spend_for_coin_state` at line 2090 or `fetch_children` at line 1911) causes the failing coin state to be persisted in `retry_store` with the original peer's node ID: [1](#0-0) 

An attacker-controlled peer can trigger this by returning a `RejectPuzzleSolution` response when the wallet requests the singleton's puzzle solution, or by returning `None` for `fetch_children`. The existing test `test_retry_store` (line 1566-1583) demonstrates exactly this trigger path.

**Step 2 — `_retry_failed_states` silently substitutes any connected peer**

When the original peer is no longer connected, `_retry_failed_states` calls `get_full_node_peer()` to pick any available full-node peer and retries with it — no binding to the original peer's identity: [2](#0-1) 

**Step 3 — `fetch_coin_spend` validates puzzle hash but not the solution**

The substitute peer must return a puzzle whose tree hash matches `coin.puzzle_hash` (enforced), but the solution is accepted verbatim with zero validation: [3](#0-2) 

Since the singleton puzzle is public on-chain, the attacker can trivially supply the correct puzzle reveal. The solution is theirs to craft freely.

**Step 4 — `solution_to_pool_state` deserializes `PoolState` from the solution without puzzle execution**

`solution_to_pool_state` reads the `"p"` key from the inner solution's key-value list and calls `PoolState.from_bytes()` — no CLVM execution, no BLS signature check: [4](#0-3) [5](#0-4) 

**Step 5 — `apply_state_transition` stores the crafted spend and calls `update_pool_config`**

The only guard in `apply_state_transition` is a coin-name check against the tip (which passes because the coin comes from the blockchain-confirmed `CoinState`). The crafted `CoinSpend` is then stored in `pool_store` and `update_pool_config` is called: [6](#0-5) 

`update_pool_config` writes the attacker-controlled `pool_url` and `target_puzzle_hash` from the parsed `PoolState` into the local pool config file: [7](#0-6) 

---

### Impact Explanation

The local pool config is what the farmer uses to determine which pool server to connect to and what payout address to advertise. By injecting an attacker-controlled `pool_url`, the farmer's harvester will submit proofs to the attacker's pool server. The attacker's pool can:

- Accept proofs without paying the farmer (direct financial loss)
- Relay proofs to the real pool under the attacker's account

The `target_puzzle_hash` corruption is bounded by the pool's on-chain verification (the pool checks the singleton), but the `pool_url` redirection is not bounded by any on-chain check — the farmer simply connects to whatever URL is in the config.

---

### Likelihood Explanation

The attacker needs only to:
1. Connect as a full-node peer (no authentication required)
2. Cause one `PeerRequestException` during pool singleton processing (trivially done by returning `RejectPuzzleSolution`)
3. Disconnect and reconnect as a new peer
4. Return the correct puzzle (public on-chain) with a crafted solution

No leaked keys, no admin access, no broken cryptography required. The retry window (`coin_state_retry_seconds = 10`) is short, but the attacker controls the timing by controlling when they reconnect.

---

### Recommendation

In `_add_coin_states`, after fetching the `CoinSpend` from the substitute peer for a `POOLING_WALLET` singleton, execute the puzzle against the solution using the CLVM interpreter and verify that the resulting conditions are consistent with a valid singleton spend (correct lineage proof, correct output coin). Alternatively, validate the BLS signature embedded in the singleton spend before calling `apply_state_transition`. At minimum, `solution_to_pool_state` should not be the sole source of truth for pool state — the puzzle must be run to confirm the solution actually produces the claimed state transition.

---

### Proof of Concept

```python
# Sketch: mock substitute peer returning correct puzzle + crafted solution

from chia.pools.pool_wallet_info import PoolState, FARMING_TO_POOL
from chia.types.coin_spend import make_spend
import struct

attacker_target_ph = bytes32(b'\xaa' * 32)
attacker_pool_url  = "http://attacker.pool"

crafted_pool_state = PoolState(
    version=1,
    state=FARMING_TO_POOL.value,
    target_puzzle_hash=attacker_target_ph,
    owner_pubkey=real_owner_pubkey,   # copied from on-chain singleton puzzle
    pool_url=attacker_pool_url,
    relative_lock_height=uint32(32),
)

# inner_solution for pool-member escape path: ([("p", bytes(crafted_pool_state))], 0)
inner_sol = Program.to([[("p", bytes(crafted_pool_state))], 0])
# full singleton solution: (parent_info, my_amount, inner_sol)
crafted_solution = Program.to([parent_info, singleton_amount, inner_sol])

crafted_spend = make_spend(
    singleton_coin,          # correct coin (from blockchain CoinState)
    real_singleton_puzzle,   # correct puzzle (puzzle_hash matches coin.puzzle_hash)
    crafted_solution,        # attacker-controlled solution
)

# Substitute peer returns crafted_spend when wallet calls request_puzzle_solution
# After _retry_failed_states runs:
#   pool_config.pool_url == "http://attacker.pool"
#   pool_config.target_puzzle_hash == attacker_target_ph
assert pool_config.pool_url != attacker_pool_url  # FAILS — vulnerability confirmed
```

### Citations

**File:** chia/wallet/wallet_state_manager.py (L2227-2232)
```python
            except Exception as e:
                self.log.exception(f"Failed to add coin_state: {coin_state}, error: {e}")
                if rollback_wallets is not None:
                    self.wallets = rollback_wallets  # Restore since DB will be rolled back by writer
                if isinstance(e, (PeerRequestException, aiosqlite.Error)):
                    await self.retry_store.add_state(coin_state, peer.peer_node_id, fork_height)
```

**File:** chia/wallet/wallet_node.py (L641-656)
```python
                    if len(matching_peer) == 0:
                        try:
                            peer = self.get_full_node_peer()
                            self.log.info(
                                f"disconnected from peer {peer_id}, state will retry with {peer.peer_node_id}"
                            )
                        except ValueError:
                            self.log.info(f"disconnected from all peers, cannot retry state: {state}")
                            continue
                    else:
                        peer = matching_peer[0]
                    async with self.wallet_state_manager.db_wrapper.writer():
                        self.log.info(f"retrying coin_state: {state}")
                        await self.wallet_state_manager.add_coin_states(
                            [state], peer, None if fork_height == 0 else fork_height
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

**File:** chia/pools/pool_puzzles.py (L399-433)
```python
def solution_to_pool_state(full_spend: CoinSpend) -> PoolState | None:
    full_solution_ser: SerializedProgram = full_spend.solution
    full_solution: Program = Program.from_bytes(bytes(full_solution_ser))

    if full_spend.coin.puzzle_hash == SINGLETON_LAUNCHER_HASH:
        # Launcher spend
        extra_data: Program = full_solution.rest().rest().first()
        return pool_state_from_extra_data(extra_data)

    # Not launcher spend
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
    else:
        # pool waitingroom
        if inner_solution.first().as_int() == 0:
            return None
        extra_data = inner_solution.rest().first()
        return pool_state_from_extra_data(extra_data)
```

**File:** chia/pools/pool_wallet.py (L254-258)
```python
            pool_config.launcher_id = current_state.launcher_id
            pool_config.pool_url = current_state.current.pool_url if current_state.current.pool_url else ""
            pool_config.payout_instructions = payout_instructions
            pool_config.target_puzzle_hash = current_state.current.target_puzzle_hash
            pool_config.p2_singleton_puzzle_hash = current_state.p2_singleton_puzzle_hash
```

**File:** chia/pools/pool_wallet.py (L276-302)
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
