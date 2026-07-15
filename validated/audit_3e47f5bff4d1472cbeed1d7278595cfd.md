### Title
Malicious Peer Can Corrupt Pool Wallet State via Unverified `RespondPuzzleSolution` Solution Bytes — (`chia/wallet/util/wallet_sync_utils.py`, `chia/pools/pool_wallet.py`)

---

### Summary

`fetch_coin_spend` verifies the puzzle hash and coin name in a `RespondPuzzleSolution` response but **never verifies the solution bytes**. In the pool wallet sync path (`_add_coin_states` → `fetch_coin_spend_for_coin_state` → `apply_state_transition`), a malicious peer can return a correctly-puzzled but arbitrarily-solutioned `CoinSpend`. `apply_state_transition` stores it without any CLVM execution, and `update_pool_config` then writes the attacker-controlled `target_puzzle_hash` and `pool_url` to the persistent pool config — diverting future pool rewards.

---

### Finding Description

**Step 1 — `fetch_coin_spend` does not verify the solution** [1](#0-0) 

The function sends `RequestPuzzleSolution` to the peer and checks:
- `puzzle.get_tree_hash() == coin.puzzle_hash` (puzzle integrity)
- `coin_name == coin_id` (coin identity)

The `solution` field is accepted verbatim with no validation against on-chain execution or any CLVM run.

**Step 2 — Pool wallet sync calls `fetch_coin_spend_for_coin_state` on the untrusted peer** [2](#0-1) 

When a `CoinState` arrives for a `POOLING_WALLET` coin with `spent_height != None` and `amount == 1`, the wallet fetches the spend from the same peer that sent the update — no independent source.

**Step 3 — `apply_state_transition` stores the crafted spend without CLVM execution** [3](#0-2) 

The only guard is a coin-name equality check (`spent_coin_name != new_state.coin.name()`). There is no signature verification, no CLVM execution of `puzzle(solution)`, and no comparison of the resulting conditions against on-chain outputs. The crafted spend is written directly to `pool_store`.

**Step 4 — `solution_to_pool_state` parses the attacker-controlled solution bytes** [4](#0-3) 

This function deserializes the solution to extract a `PoolState`. With a crafted solution, the attacker can encode any `target_puzzle_hash` and `pool_url`.

**Step 5 — `update_pool_config` persists the attacker's values** [5](#0-4) 

`get_current_state()` re-runs `solution_to_pool_state` over the stored (now-crafted) spend history and writes `target_puzzle_hash` and `pool_url` to the persistent pool config file.

---

### Impact Explanation

- **Pool reward diversion (Critical)**: The farmer uses `target_puzzle_hash` from the pool config to determine where pool rewards are sent. Overwriting it with an attacker-controlled address causes all future pool rewards to be paid to the attacker.
- **Pool URL hijack (High)**: The farmer connects to `pool_url` for authentication and payout instructions. An attacker-controlled URL enables man-in-the-middle attacks on pool protocol messages.

The corruption is **persistent** (written to the config file) and survives wallet restarts.

---

### Likelihood Explanation

Any peer the wallet connects to can execute this attack. The attacker needs:
1. A TCP connection to the wallet as a full node peer (no authentication required to connect).
2. Knowledge of the current tip singleton coin name — this is public on-chain data.
3. The ability to reconstruct the singleton puzzle (deterministic from public parameters: `launcher_id`, `genesis_challenge`, `owner_pubkey`, etc.).

The attacker does not need any private keys, admin access, or broken cryptography.

---

### Recommendation

In `fetch_coin_spend`, after receiving the puzzle and solution, execute the puzzle against the solution using the CLVM and verify that the resulting conditions match the on-chain outputs (i.e., the child coins that actually appeared on-chain). Alternatively, require the full node to provide a Merkle proof of the solution against the block's generator hash, and verify it before accepting the spend.

At minimum, `apply_state_transition` should run the singleton puzzle against the provided solution and validate the output conditions before storing the spend.

---

### Proof of Concept

```python
# Pseudocode test plan
# 1. Set up a wallet with a POOLING_WALLET (tip singleton coin known)
# 2. Create a malicious peer that:
#    a. Sends CoinState(coin=tip_singleton_coin, spent_height=100, created_height=50)
#    b. On RequestPuzzleSolution: returns the CORRECT puzzle (reconstructed from
#       launcher_id + genesis_challenge + owner_pubkey) but a CRAFTED solution
#       encoding PoolState(target_puzzle_hash=ATTACKER_PH, pool_url="https://evil.pool")
# 3. Trigger _add_coin_states with this peer
# 4. Assert: pool_config.target_puzzle_hash == ATTACKER_PH
# 5. Assert: pool_config.pool_url == "https://evil.pool"
```

The crafted solution must be structured so `solution_to_pool_state` returns the malicious `PoolState`. For a pool-member singleton, the inner solution format is `(extra_data . (0 . ()))` where `extra_data` is the serialized `PoolState` — trivially constructable without any key material.

### Citations

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

**File:** chia/wallet/wallet_state_manager.py (L2082-2094)
```python
                        if record.wallet_type is WalletType.POOLING_WALLET:
                            if coin_state.spent_height is not None and coin_state.coin.amount == uint64(1):
                                singleton_wallet: PoolWallet = self.get_wallet(
                                    id=uint32(record.wallet_id), required_type=PoolWallet
                                )
                                curr_coin_state: CoinState = coin_state

                                while curr_coin_state.spent_height is not None:
                                    cs: CoinSpend = await fetch_coin_spend_for_coin_state(curr_coin_state, peer)
                                    async with self.new_action_scope(self.tx_config, push=True) as action_scope:
                                        success = await singleton_wallet.apply_state_transition(
                                            cs, uint32(curr_coin_state.spent_height), action_scope
                                        )
```

**File:** chia/pools/pool_wallet.py (L231-259)
```python
    async def update_pool_config(self, action_scope: WalletActionScope) -> None:
        current_state: PoolWalletInfo = await self.get_current_state()
        if current_state.p2_singleton_puzzle_hash not in PoolingShareState.get_all_p2_singleton_puzzle_hashes(
            root_path=self.wallet_state_manager.root_path
        ):
            PoolingShareState(
                launcher_id=current_state.launcher_id,
                pool_url=current_state.current.pool_url if current_state.current.pool_url else "",
                payout_instructions=(await action_scope.get_puzzle_hash(self.wallet_state_manager)).hex(),
                p2_singleton_puzzle_hash=current_state.p2_singleton_puzzle_hash,
                owner_public_key=current_state.current.owner_pubkey,
                target_puzzle_hash=current_state.current.target_puzzle_hash,
                key_derivation_index=-1,
            ).add(root_path=self.wallet_state_manager.root_path)
        with PoolingShareState.acquire(
            root_path=self.wallet_state_manager.root_path,
            p2_singleton_puzzle_hash=current_state.p2_singleton_puzzle_hash,
        ) as pool_config:
            payout_instructions = pool_config.payout_instructions
            if payout_instructions == "":
                payout_instructions = (await action_scope.get_puzzle_hash(self.wallet_state_manager)).hex()
                self.log.info(f"New config entry. Generated payout_instructions puzzle hash: {payout_instructions}")

            pool_config.launcher_id = current_state.launcher_id
            pool_config.pool_url = current_state.current.pool_url if current_state.current.pool_url else ""
            pool_config.payout_instructions = payout_instructions
            pool_config.target_puzzle_hash = current_state.current.target_puzzle_hash
            pool_config.p2_singleton_puzzle_hash = current_state.p2_singleton_puzzle_hash
            pool_config.owner_public_key = current_state.current.owner_pubkey
```

**File:** chia/pools/pool_wallet.py (L261-303)
```python
    async def apply_state_transition(
        self, new_state: CoinSpend, block_height: uint32, action_scope: WalletActionScope
    ) -> bool:
        """
        Updates the Pool state (including DB) with new singleton spends.
        The DB must be committed after calling this method. All validation should be done here. Returns True iff
        the spend is a valid transition spend for the singleton, False otherwise.
        """
        tip: tuple[uint32, CoinSpend] = await self.get_tip()
        tip_spend = tip[1]

        tip_coin: Coin | None = get_most_recent_singleton_coin_from_coin_spend(tip_spend)
        assert tip_coin is not None
        spent_coin_name: bytes32 = tip_coin.name()

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
        return True
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
