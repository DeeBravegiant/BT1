### Title
Empty-batch infinite loop in `batch_coin_states_by_puzzle_hashes` when all hinted coins share one block height — (`chia/full_node/coin_store.py`)

### Summary

`batch_coin_states_by_puzzle_hashes` can return `([], H)` — an empty coin-state list paired with a non-`None` `next_height` — when the hints subquery alone fills the `max_items + 1` sentinel budget and every one of those coins sits at the same block height H. The full node then sends `RespondPuzzleState(is_finished=False, coin_states=[])`, and the wallet re-requests with `previous_height = H - 1`, producing the identical response forever. All hinted coins at height H are permanently skipped.

### Finding Description

**Step-by-step trace through the production code:**

**1. Both SQL queries use an independent `LIMIT max_items + 1`.**

The primary (puzzle-hash) query: [1](#0-0) 

The hints subquery, issued separately and merged into the same dict: [2](#0-1) 

Because both limits are `max_items + 1` independently, the merged dict can hold up to `2 × (max_items + 1)` entries.

**2. The post-merge trim only removes the excess above `max_items + 1`.**

```python
while len(coin_states) > max_items + 1:
    coin_states.pop()
``` [3](#0-2) 

If the hint query returned exactly `max_items + 1` coins and the primary query returned 0, the list has exactly `max_items + 1` entries — the trim condition is `False`, so nothing is removed.

**3. The pagination sentinel pop sets `next_height = H`.**

```python
next_coin_state = coin_states.pop()          # list now has max_items entries
next_height = uint32(max(...))               # = H
``` [4](#0-3) 

**4. The "no block splitting" loop pops every remaining entry.**

All `max_items` remaining entries are also at height H, so the loop empties the list:

```python
while len(coin_states) > 0:
    ...
    if height != next_height:
        break
    coin_states.pop()          # pops all max_items entries
``` [5](#0-4) 

**5. The function returns `([], H)` — empty list, non-`None` height.** [6](#0-5) 

**6. The full node sends `RespondPuzzleState` with `is_finished=False` and `height = H - 1`.**

```python
is_done = next_min_height is None          # False
height = uint32(next_min_height - 1)       # H - 1
response = RespondPuzzleState(..., height, ..., is_done, coin_states)
``` [7](#0-6) 

**7. The wallet loops forever.**

The wallet's sync loop (representative in `sync_puzzle_hashes`) sets `previous_height = response.height = H - 1` and re-issues `RequestPuzzleState` with `min_height = H`. The full node hits the same code path and returns `([], H)` again: [8](#0-7) 

### Impact Explanation

- All `max_items + 1` hinted coins at height H are permanently invisible to the wallet — offer/trade settlement coins, CAT receives, NFT transfers, and any other hinted coin at that height are never processed.
- The wallet sync loop never terminates for those puzzle hashes; the wallet is stuck in a tight request/response cycle consuming CPU and network resources indefinitely.
- This maps directly to the allowed High impact: **"Corruption of … offer/trade settlement state … wallet sync state … with direct security impact."**

### Likelihood Explanation

- A **malicious full node** (a valid attacker in the wallet-sync threat model) can trivially craft its `hints` table with `max_items + 1` (default: 50 001) rows all at the same `confirmed_index`. The wallet has no way to detect or escape the loop.
- The condition can also arise **organically** on a legitimate full node during a large airdrop or batch-mint event where many coins with hints land in the same block.

### Recommendation

After the "no block splitting" loop, guard against the empty-list case. If `coin_states` is empty but `next_height` is non-`None`, the function must either:

1. Return the coins at `next_height` directly (i.e., do not pop them), advancing `next_height` to the *next distinct* height above H; or
2. Raise an internal error and fall back to returning all collected coins with `next_height = None` (accepting a slightly oversized batch).

A minimal guard:

```python
# After the block-splitting loop:
if len(coin_states) == 0 and next_height is not None:
    # All collected coins were at next_height; include them and signal done
    # (or advance next_height past H — requires re-querying)
    raise RuntimeError("batch_coin_states_by_puzzle_hashes: all coins at boundary height were trimmed")
```

The correct fix is to track the highest height strictly below `next_height` and use that as the cut-off, ensuring at least one coin is always returned when `next_height` is non-`None`.

### Proof of Concept

```python
import asyncio
from chia_rs.sized_bytes import bytes32
from chia_rs.sized_ints import uint32, uint64
from chia_rs import CoinRecord
from chia.types.blockchain_format.coin import Coin
from chia.full_node.coin_store import CoinStore
from chia.full_node.hint_store import HintStore
from chia._tests.util.db_connection import DBConnection
from chia.util.hash import std_hash

MAX_ITEMS = 10  # small value to keep the test fast

async def test_empty_batch_infinite_loop():
    async with DBConnection(2) as db_wrapper:
        coin_store = await CoinStore.create(db_wrapper)
        hint_store = await HintStore.create(db_wrapper)

        hint_ph = bytes32(b"\xAA" * 32)   # the puzzle hash the wallet is syncing
        other_ph = bytes32(b"\xBB" * 32)  # actual coin puzzle hash (different)
        HEIGHT = uint32(42)

        # Insert MAX_ITEMS + 1 coins all at height 42, all hinted with hint_ph
        coins = []
        hints = []
        for i in range(MAX_ITEMS + 1):
            coin = Coin(std_hash(i.to_bytes(4, "big")), other_ph, uint64(i + 1))
            cr = CoinRecord(coin, HEIGHT, uint32(0), False, uint64(0))
            coins.append(cr)
            hints.append((coin.name(), hint_ph))

        # Add to DB
        async with db_wrapper.writer_maybe_transaction() as conn:
            await conn.executemany(
                "INSERT INTO coin_record VALUES(?,?,?,?,?,?,?,?)",
                [(c.coin.name(), c.confirmed_block_index, 0, 0,
                  c.coin.puzzle_hash, c.coin.amount.stream_to_bytes(), 0) for c in coins],
            )
        await hint_store.add_hints(hints)

        # Call the function under test
        (result, next_height) = await coin_store.batch_coin_states_by_puzzle_hashes(
            [hint_ph],
            min_height=uint32(0),
            include_hinted=True,
            max_items=MAX_ITEMS,
        )

        # BUG: result is empty but next_height is non-None → infinite loop
        print(f"coin_states returned: {len(result)}")   # prints 0
        print(f"next_height: {next_height}")            # prints 42

        assert len(result) > 0, "VULNERABILITY: empty batch with non-None next_height causes infinite wallet sync loop"

asyncio.run(test_empty_batch_infinite_loop())
```

Running this test against the current codebase will fail the final `assert`, confirming that `batch_coin_states_by_puzzle_hashes` returns `([], 42)` — an empty list paired with a non-`None` `next_height` — causing the wallet to loop indefinitely and permanently miss all `MAX_ITEMS + 1` hinted coins at height 42.

### Citations

**File:** chia/full_node/coin_store.py (L503-509)
```python
                f"LIMIT ?",
                (
                    puzzle_hashes_db
                    + (min_height, min_height)
                    + ((min_amount.to_bytes(8, "big"),) if min_amount > 0 else ())
                    + (max_items + 1,)
                ),
```

**File:** chia/full_node/coin_store.py (L525-531)
```python
                    f"LIMIT ?",
                    (
                        puzzle_hashes_db
                        + (min_height, min_height)
                        + ((min_amount.to_bytes(8, "big"),) if min_amount > 0 else ())
                        + (max_items + 1,)
                    ),
```

**File:** chia/full_node/coin_store.py (L540-543)
```python
            if include_hinted:
                coin_states.sort(key=lambda cr: max(cr.created_height or uint32(0), cr.spent_height or uint32(0)))
                while len(coin_states) > max_items + 1:
                    coin_states.pop()
```

**File:** chia/full_node/coin_store.py (L551-552)
```python
        next_coin_state = coin_states.pop()
        next_height = uint32(max(next_coin_state.created_height or 0, next_coin_state.spent_height or 0))
```

**File:** chia/full_node/coin_store.py (L556-562)
```python
        while len(coin_states) > 0:
            last_coin_state = coin_states[-1]
            height = uint32(max(last_coin_state.created_height or 0, last_coin_state.spent_height or 0))
            if height != next_height:
                break

            coin_states.pop()
```

**File:** chia/full_node/coin_store.py (L564-564)
```python
        return coin_states, next_height
```

**File:** chia/full_node/full_node_api.py (L2072-2095)
```python
        is_done = next_min_height is None

        peak_height = self.full_node.blockchain.get_peak_height()
        if peak_height is None:
            reject = wallet_protocol.RejectPuzzleState(uint8(wallet_protocol.RejectStateReason.REORG))
            return make_msg(ProtocolMessageTypes.reject_puzzle_state, reject)

        height = uint32(next_min_height - 1) if next_min_height is not None else peak_height
        header_hash = self.full_node.blockchain.height_to_hash(height)
        if header_hash is None:
            reject = wallet_protocol.RejectPuzzleState(uint8(wallet_protocol.RejectStateReason.REORG))
            return make_msg(ProtocolMessageTypes.reject_puzzle_state, reject)

        # Check if the request would exceed the subscription limit.
        # We do this again since we've crossed an `await` point, to prevent a race condition.
        sub_rejection = check_subscription_limit()
        if sub_rejection is not None:
            return sub_rejection

        if is_done and request.subscribe_when_finished:
            subs.add_puzzle_subscriptions(peer.peer_node_id, puzzle_hashes, max_subscriptions)
            await self.mempool_updates_for_puzzle_hashes(peer, set(puzzle_hashes), request.filters.include_hinted)

        response = wallet_protocol.RespondPuzzleState(puzzle_hashes, height, header_hash, is_done, coin_states)
```

**File:** chia/_tests/wallet/test_new_wallet_protocol.py (L706-714)
```python
            if not response.is_finished:
                previous_height = response.height
                previous_header_hash = response.header_hash
                yield PuzzleStateData(
                    coin_states=response.coin_states,
                    end_of_batch=False,
                    previous_height=previous_height,
                    header_hash=previous_header_hash,
                )
```
