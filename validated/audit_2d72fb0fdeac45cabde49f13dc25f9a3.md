The call chain is fully traceable. Here is the complete analysis:

## Call Chain Trace

**Entry point:** `coin_state_update` in `wallet_node_api.py` passes the request directly to the queue with no trust check. [1](#0-0) 

The queue dispatches to `state_update_received`, which calls `add_states_from_peer` passing `fork_height` and `height` from the attacker-controlled message. [2](#0-1) 

**Untrusted peer path in `add_states_from_peer`:** When `fork_height` is not None (as in a `CoinStateUpdate`), states go to the race cache. When the race cache is later drained (on `new_peak_wallet`), `add_states_from_peer` is called again with `fork_height=None`, which triggers `validate_and_add` → `_collect_valid_states` → `validate_received_state_from_peer`. [3](#0-2) 

**`validate_received_state_from_peer` for a spent coin:** This function validates the coin was actually created (Merkle proof of additions root) and actually spent (Merkle proof of removals root) in a block that is part of the weight-proof-validated chain. This is a real, cryptographic guard. [4](#0-3) 

**After validation passes — the gap:** In `_add_coin_states`, once the coin state is accepted, the DATA_LAYER branch immediately calls `fetch_coin_spend_for_coin_state(coin_state, peer)` on the **same untrusted peer** that sent the `CoinStateUpdate`. [5](#0-4) 

**`fetch_coin_spend` validation:** This function only checks that the returned puzzle hashes to `coin.puzzle_hash` and that the coin name matches. It does **not** validate the solution against any on-chain data. [6](#0-5) 

**`singleton_removed` trusts the spend:** It runs the puzzle with the peer-supplied solution via `run_with_cost`, then extracts `root` and `inner_puzzle_hash` from the resulting CREATE_COIN condition's hints, and writes them directly to the DL store. [7](#0-6) 

## Exploitability Assessment

For the attack to work, the attacker needs:

1. **A real DL singleton coin that was actually spent on-chain.** The `validate_received_state_from_peer` Merkle proof check is a genuine cryptographic barrier — the attacker cannot fabricate a coin state for a coin that was not actually spent. The coin must exist and be spent in the canonical chain.

2. **The ability to return a fabricated solution.** The DL singleton puzzle is a public, deterministic CLVM program. The attacker knows the full puzzle (they can compute it from the coin's puzzle hash, since the DL singleton puzzle is curried with public parameters). `fetch_coin_spend` only checks `puzzle.get_tree_hash() == coin.puzzle_hash` — it does not validate the solution. The attacker returns the correct puzzle reveal but a crafted solution that, when run through the DL singleton puzzle, produces a CREATE_COIN condition with attacker-chosen `root` and `inner_puzzle_hash` hints.

3. **The wallet must be tracking that singleton.** The `record.wallet_type == WalletType.DATA_LAYER` branch only fires if the wallet has a local record for the coin.

The constraint in point 1 is significant: the attacker cannot target an *unspent* singleton. They must wait for a real spend to occur (or find a historical one). However, once a spend occurs, any untrusted peer that the wallet connects to can corrupt the wallet's view of the post-spend root.

## Impact

The DL store is updated with an attacker-chosen `root` and `inner_puzzle_hash`. This corrupts the wallet's local view of the DataLayer singleton's current state. Downstream effects include: the wallet accepting fabricated data proofs as valid (since it checks against the wrong root), rejecting legitimate data, and generating incorrect inclusion/exclusion proofs for any application relying on the DL wallet's root state.

This is a **real, reachable vulnerability** matching the "High: Corruption of Data Layer root/store state with direct security impact" category.

---

### Title
Untrusted Peer Can Corrupt DataLayer Singleton Root via Fabricated `fetch_coin_spend` Response — (`chia/wallet/wallet_state_manager.py`)

### Summary
After `validate_received_state_from_peer` confirms a DL singleton coin was genuinely spent on-chain, `_add_coin_states` fetches the spend data from the **same untrusted peer** without validating the solution against the chain. The peer can return a fabricated solution that makes `singleton_removed` write an attacker-chosen root into the DL store.

### Finding Description
In `_add_coin_states` (`wallet_state_manager.py`, lines 2124–2130), when `record.wallet_type == WalletType.DATA_LAYER`, the code calls `fetch_coin_spend_for_coin_state(coin_state, peer)` where `peer` is the untrusted peer that originated the `CoinStateUpdate`. `fetch_coin_spend` (`wallet_sync_utils.py`, lines 336–352) only validates that the returned puzzle hashes to `coin.puzzle_hash`; it does not validate the solution. `singleton_removed` (`data_layer_wallet.py`, lines 798–861) then runs the puzzle with the peer-supplied solution via `run_with_cost` and extracts `root` and `inner_puzzle_hash` from the resulting CREATE_COIN condition's hints, writing them to the DL store unconditionally.

### Impact Explanation
The wallet's DL store records an attacker-chosen data root for the singleton. Any application or user relying on the wallet's DL root for data verification will operate against a corrupted root, enabling acceptance of fabricated data proofs or rejection of legitimate ones.

### Likelihood Explanation
Requires the attacker to observe a real on-chain spend of a tracked DL singleton (a public blockchain event) and connect to the victim wallet as an untrusted full-node peer (normal network operation). The DL singleton puzzle is public, so crafting a solution that produces arbitrary CREATE_COIN hints is straightforward for anyone with CLVM knowledge.

### Recommendation
After `fetch_coin_spend` returns, validate the spend against the on-chain removals Merkle root (already fetched during `validate_received_state_from_peer`) before passing it to `singleton_removed`. Alternatively, re-derive the expected post-spend root from the validated block data rather than trusting the peer-supplied solution.

### Proof of Concept
1. Observe a real DL singleton spend at height H on the public chain.
2. Connect to the victim wallet as an untrusted full-node peer.
3. Send `CoinStateUpdate` with the correct coin state (`spent_height=H`).
4. When the wallet calls `request_puzzle_solution` for the spent coin, respond with the correct puzzle reveal (matching `coin.puzzle_hash`) but a crafted solution that, when run through the DL singleton puzzle, emits `CREATE_COIN <attacker_puzzle_hash> <odd_amount> [<launcher_id>, <fake_root>, <fake_inner_puzhash>]`.
5. Assert that `dl_store.get_latest_singleton(launcher_id).root == fake_root`.

### Citations

**File:** chia/wallet/wallet_node_api.py (L192-194)
```python
    @metadata.request(peer_required=True, execute_task=True)
    async def coin_state_update(self, request: wallet_protocol.CoinStateUpdate, peer: WSChiaConnection) -> None:
        await self.wallet_node.new_peak_queue.full_node_state_updated(request, peer)
```

**File:** chia/wallet/wallet_node.py (L1059-1076)
```python
            if trusted:
                async with self.wallet_state_manager.db_wrapper.writer():
                    self.log.info(
                        f"new coin state received ({idx}-{idx + len(batch.entries) - 1}/ {len(updated_coin_states)})"
                    )
                    if not await self.wallet_state_manager.add_coin_states(batch.entries, peer, fork_height):
                        return False
            elif fork_height is not None:
                cache.add_states_to_race_cache(batch.entries)
            else:
                while len(all_tasks) >= target_concurrent_tasks:
                    all_tasks = [task for task in all_tasks if not task.done()]
                    await asyncio.sleep(0.1)
                    if self._shut_down:
                        self.log.info("Terminating receipt and validation due to shut down request")
                        await asyncio.gather(*all_tasks)
                        return False
                all_tasks.append(create_referenced_task(validate_and_add(batch.entries, idx)))
```

**File:** chia/wallet/wallet_node.py (L1090-1104)
```python
    async def state_update_received(self, request: CoinStateUpdate, peer: WSChiaConnection) -> None:
        # This gets called every time there is a new coin or puzzle hash change in the DB
        # that is of interest to this wallet. It is not guaranteed to come for every height. This message is guaranteed
        # to come before the corresponding new_peak for each height. We handle this differently for trusted and
        # untrusted peers. For trusted, we always process the state, and we process reorgs as well.
        for coin in request.items:
            self.log.info(f"request coin: {coin.coin.name().hex()}{coin}")

        async with self.wallet_state_manager.lock:
            await self.add_states_from_peer(
                request.items,
                peer,
                request.fork_height,
                request.height,
            )
```

**File:** chia/wallet/wallet_node.py (L1614-1644)
```python
        if spent_height is not None:
            # request header block for created height
            cached_spent_state_block = peer_request_cache.get_block(spent_height)
            if cached_spent_state_block is None:
                spent_state_block = await request_and_validate_header_block(peer, spent_height, self.log)
                if spent_state_block is None:
                    return False
                peer_request_cache.add_to_blocks(spent_state_block)
            else:
                spent_state_block = cached_spent_state_block
            if spent_state_block.foliage_transaction_block is None:
                return False
            validate_removals_result = await request_and_validate_removals(
                peer,
                spent_state_block.height,
                spent_state_block.header_hash,
                coin_state.coin.name(),
                spent_state_block.foliage_transaction_block.removals_root,
            )
            if validate_removals_result is None:
                return False
            if validate_removals_result is False:
                self.log.warning("Validate false 3")
                await peer.close(9999)
                return False
            validated = await self.validate_block_inclusion(spent_state_block, peer, peer_request_cache)
            if not validated:
                return False
        peer_request_cache.add_to_states_validated(coin_state)

        return True
```

**File:** chia/wallet/wallet_state_manager.py (L2124-2130)
```python
                        if record.wallet_type == WalletType.DATA_LAYER:
                            singleton_spend = await fetch_coin_spend_for_coin_state(coin_state, peer)
                            dl_wallet = self.get_wallet(id=uint32(record.wallet_id), required_type=DataLayerWallet)
                            await dl_wallet.singleton_removed(
                                singleton_spend,
                                uint32(coin_state.spent_height),
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

**File:** chia/data_layer/data_layer_wallet.py (L798-861)
```python
    async def singleton_removed(self, parent_spend: CoinSpend, height: uint32) -> None:
        parent_name = parent_spend.coin.name()
        puzzle = parent_spend.puzzle_reveal
        solution = parent_spend.solution

        matched, _ = match_dl_singleton(puzzle)
        if matched:
            self.log.info(f"DL singleton removed: {parent_spend.coin}")
            singleton_record: SingletonRecord | None = await self.wallet_state_manager.dl_store.get_singleton_record(
                parent_name
            )
            if singleton_record is None:
                self.log.warning(f"DL wallet received coin it does not have parent for. Expected parent {parent_name}.")
                return

            # Information we need to create the singleton record
            full_puzzle_hash: bytes32
            amount: uint64
            root: bytes32
            inner_puzzle_hash: bytes32

            conditions = run_with_cost(puzzle, self.wallet_state_manager.constants.MAX_BLOCK_COST_CLVM, solution)[
                1
            ].as_python()
            found_singleton: bool = False
            for condition in conditions:
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
                    found_singleton = True
                    break

            if not found_singleton:
                self.log.warning(f"Singleton with launcher ID {singleton_record.launcher_id} was melted")
                return

            new_singleton = Coin(parent_name, full_puzzle_hash, amount)
            timestamp = await self.wallet_state_manager.wallet_node.get_timestamp_for_height(height)
            await self.wallet_state_manager.dl_store.add_singleton_record(
                SingletonRecord(
                    coin_id=new_singleton.name(),
                    launcher_id=singleton_record.launcher_id,
                    root=root,
                    inner_puzzle_hash=inner_puzzle_hash,
                    confirmed=True,
                    confirmed_at_height=height,
                    timestamp=timestamp,
                    lineage_proof=LineageProof(
                        parent_name,
                        create_host_layer_puzzle(inner_puzzle_hash, root).get_tree_hash_precalc(inner_puzzle_hash),
                        amount,
                    ),
                    generation=uint32(singleton_record.generation + 1),
                )
            )
```
