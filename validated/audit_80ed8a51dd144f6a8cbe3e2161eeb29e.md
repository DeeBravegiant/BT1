### Title
Uncaught `ValueError` in `singleton_removed` Permanently Drops DL Singleton Sync State — (`chia/data_layer/data_layer_wallet.py`)

### Summary
`singleton_removed` in `DataLayerWallet` catches only `IndexError` when parsing CREATE_COIN hint fields, but `bytes32()` raises `ValueError` for any atom that is not exactly 32 bytes. An attacker who controls a DL singleton that a victim wallet tracks can submit a valid on-chain spend with a malformed hint (e.g., a 31-byte second element), causing an uncaught `ValueError` that propagates to `_add_coin_states`, which permanently discards the coin state without retry, leaving the wallet unable to track the singleton's lineage ever again.

### Finding Description

The vulnerable block is in `singleton_removed`: [1](#0-0) 

```python
try:
    root = bytes32(condition[3][1])
    inner_puzzle_hash = bytes32(condition[3][2])
except IndexError:
    self.log.warning(...)
    return
```

`bytes32` (from `chia_rs.sized_bytes`) raises `ValueError` — not `IndexError` — when given an atom that is not exactly 32 bytes. The `except IndexError` guard at line 830 does not catch `ValueError`, so any non-32-byte atom at `condition[3][1]` or `condition[3][2]` causes an unhandled exception.

The `conditions` list is produced by running the puzzle/solution pair through `run_with_cost(...).as_python()`: [2](#0-1) 

The hint field of a CREATE_COIN condition is arbitrary on-chain data. Consensus does not validate hint content or length. A valid singleton spend can include a CREATE_COIN condition with an odd amount and a hint list whose second element is any byte string (e.g., 31 bytes).

The `ValueError` propagates to the outer `except Exception` handler in `_add_coin_states`: [3](#0-2) 

```python
except Exception as e:
    self.log.exception(...)
    if isinstance(e, (PeerRequestException, aiosqlite.Error)):
        await self.retry_store.add_state(coin_state, peer.peer_node_id, fork_height)
    else:
        await self.retry_store.remove_state(coin_state)  # ← permanent drop
    continue
```

Because `ValueError` is neither `PeerRequestException` nor `aiosqlite.Error`, the coin state is **removed from the retry store** (line 2234) and never reprocessed. The DB transaction is rolled back by the `async with self.db_wrapper.writer():` context manager, so no singleton record is written. The wallet permanently loses lineage tracking for that DL store.

### Impact Explanation

The wallet permanently fails to process the singleton removal. The coin state is dropped without retry. The wallet's DL store record is stuck at the previous generation, blocking all future state updates for that singleton. This matches the High impact category: **permanent inability to process Data Layer sync updates**.

### Likelihood Explanation

The attack requires:
1. The attacker creates a DL singleton (permissionless).
2. A victim wallet subscribes to / tracks that singleton (normal Data Layer usage — wallets subscribe to external DL stores to read their data).
3. The attacker spends the singleton with a CREATE_COIN hint where `condition[3][1]` is not 32 bytes.

Step 3 produces a fully valid on-chain spend — consensus does not validate hint byte lengths. The attacker does not need any keys belonging to the victim. This is a realistic scenario in any Data Layer deployment where wallets subscribe to third-party stores.

### Recommendation

Extend the `except` clause to also catch `ValueError`:

```python
try:
    root = bytes32(condition[3][1])
    inner_puzzle_hash = bytes32(condition[3][2])
except (IndexError, ValueError):
    self.log.warning(
        f"Parent {parent_name} with launcher {singleton_record.launcher_id} "
        "did not hint its child properly"
    )
    return
```

Similarly, the `bytes32(condition[1])` call at line 825 and `bytes32(condition[3][2])` at line 829 should both be inside the guarded block, since any of them can raise `ValueError` on malformed on-chain data. [4](#0-3) 

### Proof of Concept

1. Attacker launches a DL singleton on-chain (standard `DataLayerWallet.create_new_dl`).
2. Victim wallet subscribes to the attacker's launcher ID.
3. Attacker crafts a singleton spend whose inner puzzle outputs:
   ```
   (CREATE_COIN <puzzle_hash> 1 (list <coin_id> <31-byte-atom> <32-byte-atom>))
   ```
   The 31-byte atom at position `[3][1]` is the malformed root hint.
4. The spend is valid on-chain (consensus ignores hint content).
5. When the victim wallet processes the spend, `singleton_removed` is called, `bytes32(<31-byte-atom>)` raises `ValueError`, the exception propagates to `_add_coin_states`, the coin state is permanently removed from the retry store, and the wallet never tracks the singleton's next generation.
6. Assert: `dl_store.get_latest_singleton(launcher_id)` still returns the pre-spend record; no new record is written; no retry is scheduled.

### Citations

**File:** chia/data_layer/data_layer_wallet.py (L819-821)
```python
            conditions = run_with_cost(puzzle, self.wallet_state_manager.constants.MAX_BLOCK_COST_CLVM, solution)[
                1
            ].as_python()
```

**File:** chia/data_layer/data_layer_wallet.py (L824-835)
```python
                if condition[0] == ConditionOpcode.CREATE_COIN and int.from_bytes(condition[2], "big") % 2 == 1:
                    full_puzzle_hash = bytes32(condition[1])
                    amount = uint64(int.from_bytes(condition[2], "big"))
                    try:
                        root = bytes32(condition[3][1])
                        inner_puzzle_hash = bytes32(condition[3][2])
                    except IndexError:
                        self.log.warning(
                            f"Parent {parent_name} with launcher {singleton_record.launcher_id} "
                            "did not hint its child properly"
                        )
                        return
```

**File:** chia/wallet/wallet_state_manager.py (L2227-2235)
```python
            except Exception as e:
                self.log.exception(f"Failed to add coin_state: {coin_state}, error: {e}")
                if rollback_wallets is not None:
                    self.wallets = rollback_wallets  # Restore since DB will be rolled back by writer
                if isinstance(e, (PeerRequestException, aiosqlite.Error)):
                    await self.retry_store.add_state(coin_state, peer.peer_node_id, fork_height)
                else:
                    await self.retry_store.remove_state(coin_state)
                continue
```
