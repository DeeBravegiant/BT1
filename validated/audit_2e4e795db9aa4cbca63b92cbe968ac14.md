Now I have a complete picture of the code. Let me synthesize the findings.

**Key code facts established:**

1. `fetch_coin_spend` validates puzzle hash but **not the solution** — lines 343–346 of `wallet_sync_utils.py`
2. `singleton_removed` calls `run_with_cost(puzzle, MAX_BLOCK_COST_CLVM, solution)` with the unvalidated solution — line 819 of `data_layer_wallet.py`
3. Root is extracted from `condition[3][1]` (the hint field) and stored directly in `SingletonRecord` — lines 828, 849
4. The NFT state layer solution structure is simply `(inner_solution)` — confirmed by `solution_for_metadata_layer` in `metadata_outer_puzzle.py` line 27–28 and the test at `test_nft_puzzles.py` line 85–86
5. The DL wallet constructs `db_layer_sol = Program.to([inner_sol])` — line 540 of `data_layer_wallet.py`
6. The `CREATE_COIN` hint `[launcher_id, root_hash, inner_puzzle_hash]` comes from the **inner puzzle's conditions**, not from the NFT state layer metadata — lines 514–523 of `data_layer_wallet.py`

**Attack feasibility analysis:**

The DL singleton puzzle is `SINGLETON_TOP_LAYER_MOD(singleton_struct, NFT_STATE_LAYER_MOD(..., ACS_MU_PH, innerpuz))`. The solution is `(lineage_proof amount (inner_sol))`. For a crafted solution to succeed without `EvalError`:

- **Singleton lineage proof check**: The singleton CLVM checks `sha256(parent_name, inner_puzzle_hash, amount) == coin_id`. All values are public on-chain, so the attacker can construct a valid lineage proof.
- **NFT state layer**: Passes through inner puzzle conditions unchanged; solution is just `(inner_sol)`.
- **Standard wallet inner puzzle**: Produces conditions from a delegated puzzle. `run_with_cost` does **not** validate BLS signatures — it only outputs `AGG_SIG_ME` as a condition. The attacker can provide a delegated puzzle that outputs `CREATE_COIN` with `[launcher_id, fake_root, inner_puzzle_hash]` as hints.

The `singleton_removed` code only looks for `CREATE_COIN` conditions, ignoring `AGG_SIG_ME`. So the fake root is extracted and stored.

---

### Title
Malicious Peer Can Inject Arbitrary Data Layer Root via Unvalidated Solution in `fetch_coin_spend` — (`chia/wallet/util/wallet_sync_utils.py`)

### Summary
`fetch_coin_spend` validates the puzzle hash of a peer's `RespondPuzzleSolution` but accepts the solution without any validation. A malicious peer returns the correct puzzle (matching `coin.puzzle_hash`) paired with a crafted solution. `DataLayerWallet.singleton_removed` then executes `run_with_cost(puzzle, MAX_BLOCK_COST_CLVM, solution)` on the crafted spend, extracts the root from `condition[3][1]`, and stores it in `SingletonRecord` — with no cross-check against on-chain state.

### Finding Description

**Root cause — `fetch_coin_spend`** validates only puzzle hash and coin name, not the solution: [1](#0-0) 

The returned `CoinSpend` (with attacker-controlled solution) flows directly into `singleton_removed` via: [2](#0-1) 

**Root extraction — `singleton_removed`** runs the puzzle with the unvalidated solution and blindly trusts the hint: [3](#0-2) 

The extracted root is stored directly: [4](#0-3) 

**Crafted solution construction:**

The DL singleton puzzle is `SINGLETON_TOP_LAYER_MOD(singleton_struct, NFT_STATE_LAYER_MOD(metadata, ACS_MU_PH, innerpuz))`. The NFT state layer solution is simply `(inner_solution)`: [5](#0-4) 

The `CREATE_COIN` hint `[launcher_id, root_hash, inner_puzzle_hash]` originates from the inner puzzle's conditions: [6](#0-5) 

An attacker crafts a solution with:
1. **Valid lineage proof** — all fields (`parent_name`, `inner_puzzle_hash`, `amount`) are public on-chain; the singleton CLVM lineage check is satisfiable.
2. **Crafted delegated puzzle** — a CLVM program that outputs `(51 <any_puzzle_hash> <odd_amount> (<launcher_id> <fake_root> <inner_puzzle_hash>))`.
3. **Any `original_public_key`** — the standard wallet puzzle outputs `AGG_SIG_ME` as a condition but does not validate the signature in CLVM; `run_with_cost` does not check signatures.

The NFT state layer passes inner conditions through unchanged. `singleton_removed` finds the `CREATE_COIN` with odd amount, reads `condition[3][1]` as the root, and stores `fake_root` in `SingletonRecord`.

### Impact Explanation

The wallet's `SingletonRecord.root` is permanently set to an attacker-chosen value, diverging from the actual on-chain committed root. Consequences:
- The wallet serves incorrect Merkle proofs for DL data (wrong root → all proofs invalid or fabricated).
- DL offer/trade settlement decisions are made against the fake root, potentially causing the wallet to accept or reject offers incorrectly.
- Subsequent `create_update_state_spend` calls use the corrupted root, producing invalid spends.
- The wallet cannot self-correct without a full resync from a trusted peer.

This directly matches the scope's **High** impact: *"Corruption of … Data Layer root/store state with direct security impact."*

### Likelihood Explanation

The attacker must operate a malicious full node that the victim wallet connects to. This is realistic for:
- Light wallets connecting to public/third-party full nodes.
- Adversary-in-the-middle scenarios on the peer connection.

No key material, admin access, or cryptographic break is required. The attacker only needs the victim to connect to their node while a DL singleton coin is spent.

### Recommendation

In `fetch_coin_spend`, after receiving the solution, verify it against the actual on-chain spend by cross-checking the child coin's puzzle hash and amount against the blockchain (e.g., via `get_coin_state` on the child). Alternatively, in `singleton_removed`, after extracting `full_puzzle_hash` and `root`, verify that `Coin(parent_name, full_puzzle_hash, amount).name()` matches a known child coin state returned by the full node — not just the peer that provided the solution.

### Proof of Concept

```python
# Mock peer returns correct puzzle + crafted solution
fake_root = bytes32(b"\xff" * 32)
launcher_id = <known_launcher_id>
inner_puzzle_hash = <known_inner_puzzle_hash>

# Crafted delegated puzzle: outputs CREATE_COIN with fake root hint
crafted_delegated = Program.to((1, [[
    51,                          # CREATE_COIN
    <any_odd_puzzle_hash>,       # full_puzzle_hash (condition[1])
    1,                           # odd amount (condition[2])
    [launcher_id, fake_root, inner_puzzle_hash],  # hints (condition[3])
]]))

# Craft full singleton solution with valid lineage proof
crafted_inner_sol = standard_wallet_solution(crafted_delegated, Program.to([]))
crafted_nft_layer_sol = Program.to([crafted_inner_sol])
crafted_full_sol = Program.to([valid_lineage_proof, amount, crafted_nft_layer_sol])

# Peer returns: correct puzzle (hash matches), crafted solution
# fetch_coin_spend passes (puzzle hash check passes, solution not checked)
# singleton_removed stores fake_root in SingletonRecord

assert stored_singleton_record.root == fake_root  # NOT the on-chain root
```

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

**File:** chia/data_layer/data_layer_wallet.py (L514-523)
```python
        primaries = [
            CreateCoin(
                announce_only.get_tree_hash() if announce_new_state else new_puz_hash,
                singleton_record.lineage_proof.amount if new_amount is None else new_amount,
                [
                    launcher_id,
                    root_hash,
                    announce_only.get_tree_hash() if announce_new_state else new_puz_hash,
                ],
            )
```

**File:** chia/data_layer/data_layer_wallet.py (L819-837)
```python
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
```

**File:** chia/data_layer/data_layer_wallet.py (L845-860)
```python
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
```

**File:** chia/wallet/nft_wallet/metadata_outer_puzzle.py (L27-28)
```python
def solution_for_metadata_layer(inner_solution: Program) -> Program:
    return Program.to([inner_solution])
```
