I have the key code. Let me analyze the exact logic in `handle_did` and the full call chain.

The critical block is at lines 1390–1409 of `wallet_state_manager.py`:

```python
if derivation_record is None:
    # Check if it was owned by us
    removed_wallet_ids = []
    for wallet in self.wallets.values():
        if not isinstance(wallet, DIDWallet):
            continue
        if (
            wallet.did_info.origin_coin is not None
            and launch_id == wallet.did_info.origin_coin.name()
            and not wallet.did_info.sent_recovery_transaction
        ):
            await self.delete_wallet(wallet.id())
            removed_wallet_ids.append(wallet.id())
    for remove_id in removed_wallet_ids:
        self.wallets.pop(remove_id)
```

Now let me trace the full preconditions for reaching this block:

**Step 1 — `derivation_record` source:** [1](#0-0) 

The hint is extracted from `compute_spend_hints_and_additions(coin_spend)`, and `coin_spend` is fetched from the peer via `fetch_coin_spend_for_coin_state(parent_coin_state, peer)`. [2](#0-1) 

**Step 2 — `launch_id` source:** [3](#0-2) 

`launch_id` is extracted from `parent_data.singleton_struct`, which itself comes from `match_did_puzzle` applied to the puzzle reveal in the peer-supplied `coin_spend`.

**Step 3 — Parent coin state is peer-supplied:** [4](#0-3) 

The parent coin state is fetched from the same peer. There is no independent blockchain verification of the coin spend's puzzle reveal or solution.

---

**Attack path analysis:**

A malicious peer controls:
- The `CoinState` it sends to the wallet
- The `parent_coin_state` it returns when queried
- The `coin_spend` it returns for the parent coin

The peer can craft a consistent set:
1. Send a coin state for any coin C
2. Return a parent coin state for C's parent with `spent_height == coin_state.created_height` (satisfying the assertion at line 915)
3. Return a coin spend whose puzzle reveal decodes as a DID puzzle (`match_did_puzzle` succeeds) with `singleton_struct` embedding the victim's `launch_id` X
4. Include in the solution a CREATE_COIN condition with a hint that does **not** match any of the wallet's derivation puzzle hashes

Result: `derivation_record = None`, `launch_id == X`, `sent_recovery_transaction == False` → the DIDWallet is deleted.

**Guard analysis:**

The only guard is `not wallet.did_info.sent_recovery_transaction`. For any normal DIDWallet that has not initiated a recovery, this is `False`, so the guard does not protect against the attack. [5](#0-4) 

There is no signature check, no on-chain proof of the coin spend, and no verification that the puzzle reveal hashes to the parent coin's actual on-chain puzzle hash before the deletion logic runs.

**Recovery consideration:**

`find_lost_did` can re-create the wallet, but it requires manual user action and the attack can be repeated continuously by the malicious peer, keeping the DIDWallet perpetually deleted and the DID singleton unspendable. [6](#0-5) 

---

### Title
Malicious Peer Can Delete Legitimate DIDWallet via Crafted CoinState with Non-Matching Hint — (`chia/wallet/wallet_state_manager.py`)

### Summary
`handle_did` unconditionally deletes a `DIDWallet` when it receives a coin state whose peer-supplied coin spend contains a DID puzzle matching the wallet's `launch_id` but a hint that does not resolve to any local derivation record. Because the coin spend (including its hint) is fetched from the peer without cryptographic verification against on-chain state, a malicious peer can fabricate this condition for any known `launch_id`.

### Finding Description
In `WalletStateManager.handle_did`, when `derivation_record is None` (hint not in local puzzle store), the code iterates over all `DIDWallet` instances and deletes any whose `origin_coin.name()` matches the `launch_id` extracted from the peer-supplied coin spend, provided `sent_recovery_transaction` is `False`.

The `launch_id` and the hint both originate from a `coin_spend` returned by the peer (`fetch_coin_spend_for_coin_state`). No validation is performed to confirm that the coin spend is the actual spend recorded on-chain, that the puzzle reveal hashes to the parent coin's on-chain puzzle hash, or that the solution/hint is authentic. A malicious peer can therefore supply a fabricated coin spend with any `launch_id` and any hint. [7](#0-6) 

### Impact Explanation
An unprivileged malicious peer causes the wallet to call `delete_wallet` and `wallets.pop` on a legitimate `DIDWallet`. The DID singleton becomes unspendable from the wallet's perspective. The user must manually invoke `find_lost_did` to recover, but the attack can be repeated indefinitely, keeping the wallet in a permanently disrupted state with respect to DID control. This constitutes unauthorized singleton state corruption and long-lived inability to spend the DID singleton.

### Likelihood Explanation
Any peer the wallet connects to can execute this attack. Wallets connect to peers from the peer discovery network; a single malicious peer in the peer list is sufficient. The attack requires no keys, no privileged access, and no user interaction beyond the wallet being online.

### Recommendation
Before executing the deletion branch, verify that the coin spend is authentic:
- Check that `sha256(coin_spend.puzzle_reveal) == parent_coin_state.coin.puzzle_hash` before trusting the puzzle reveal.
- Do not delete a `DIDWallet` based solely on peer-supplied data. Require the coin state to be confirmed on-chain (e.g., `coin_state.spent_height is not None` and independently verified) before treating the DID as transferred away.
- Consider requiring a second confirmation source or a local re-derivation check before wallet deletion.

### Proof of Concept
1. Create a wallet with a `DIDWallet` whose `origin_coin.name() == X` and `sent_recovery_transaction == False`.
2. Connect a malicious peer to the wallet node.
3. The peer sends a `CoinStateUpdate` containing a `CoinState` for an arbitrary coin C.
4. When the wallet queries the parent of C, the peer returns a fabricated `parent_coin_state` with `spent_height` equal to C's `created_height`.
5. When the wallet fetches the coin spend for the parent, the peer returns a fabricated `CoinSpend` whose puzzle reveal decodes as a valid DID puzzle with `singleton_struct` embedding `launch_id = X`, and whose solution contains a CREATE_COIN condition with a hint `H` where `puzzle_store.get_derivation_record_for_puzzle_hash(H)` returns `None`.
6. `handle_did` sets `derivation_record = None`, finds the `DIDWallet` with `origin_coin.name() == X`, calls `delete_wallet` and `wallets.pop`.
7. Assert `X not in wallet_state_manager.wallets` — the DIDWallet is gone without owner authorization. [8](#0-7)

### Citations

**File:** chia/wallet/wallet_state_manager.py (L908-914)
```python
        response: list[CoinState] = await self.wallet_node.get_coin_state(
            [coin_state.coin.parent_coin_info], peer=peer, fork_height=fork_height
        )
        if len(response) == 0:
            self.log.warning(f"Could not find a parent coin with ID: {coin_state.coin.parent_coin_info.hex()}")
            return None, None
        parent_coin_state = response[0]
```

**File:** chia/wallet/wallet_state_manager.py (L917-917)
```python
        coin_spend = await fetch_coin_spend_for_coin_state(parent_coin_state, peer)
```

**File:** chia/wallet/wallet_state_manager.py (L1385-1409)
```python
        hinted_coin = compute_spend_hints_and_additions(coin_spend)[0][coin_state.coin.name()]
        assert hinted_coin.hint is not None, f"hint missing for coin {hinted_coin.coin}"
        derivation_record = await self.puzzle_store.get_derivation_record_for_puzzle_hash(hinted_coin.hint)

        launch_id = bytes32(parent_data.singleton_struct.rest().first().as_atom())
        if derivation_record is None:
            self.log.info(f"Received state for the coin that doesn't belong to us {coin_state}")
            # Check if it was owned by us
            # If the puzzle inside is no longer recognised then delete the wallet associated
            removed_wallet_ids = []
            for wallet in self.wallets.values():
                if not isinstance(wallet, DIDWallet):
                    continue
                if (
                    wallet.did_info.origin_coin is not None
                    and launch_id == wallet.did_info.origin_coin.name()
                    and not wallet.did_info.sent_recovery_transaction
                ):
                    await self.delete_wallet(wallet.id())
                    removed_wallet_ids.append(wallet.id())
            for remove_id in removed_wallet_ids:
                self.wallets.pop(remove_id)
                self.log.info(f"Removed DID wallet {remove_id}, Launch_ID: {launch_id.hex()}")
                self.state_changed("wallet_removed", remove_id)
            return None
```

**File:** chia/wallet/wallet_state_manager.py (L3302-3345)
```python
    async def find_lost_did(
        self,
        *,
        coin_id: bytes32,
        override_recovery_list_hash: bytes32 | None = None,
        override_num_verification: uint16 | None = None,
        override_metadata: dict[str, str] | None = None,
    ) -> None:
        # Get coin state
        peer = self.wallet_node.get_full_node_peer()
        coin_spend, coin_state = await self.get_latest_singleton_coin_spend(peer, coin_id)
        uncurried = uncurry_puzzle(coin_spend.puzzle_reveal)
        curried_args = match_did_puzzle(uncurried.mod, uncurried.args)
        if curried_args is None:
            raise ValueError("The coin is not a DID.")
        p2_puzzle, recovery_list_hash, num_verification, singleton_struct, metadata = curried_args
        num_verification_int: uint16 | None = uint16(num_verification.as_int())
        assert num_verification_int is not None
        did_data: DIDCoinData = DIDCoinData(
            p2_puzzle,
            bytes32(recovery_list_hash.as_atom()) if recovery_list_hash != Program.NIL else None,
            num_verification_int,
            singleton_struct,
            metadata,
            get_inner_puzzle_from_singleton(coin_spend.puzzle_reveal),
            coin_state,
        )
        hinted_coins, _ = compute_spend_hints_and_additions(coin_spend)
        # Hint is required, if it doesn't have any hint then it should be invalid
        hint: bytes32 | None = None
        for hinted_coin in hinted_coins.values():
            if hinted_coin.coin.amount % 2 == 1 and hinted_coin.hint is not None:
                hint = hinted_coin.hint
                break
        derivation_record = None
        if hint is not None:
            derivation_record = await self.puzzle_store.get_derivation_record_for_puzzle_hash(hint)
        if derivation_record is None:
            # This is an invalid DID, check if we are owner
            derivation_record = await self.puzzle_store.get_derivation_record_for_puzzle_hash(p2_puzzle.get_tree_hash())

        launcher_id = bytes32(singleton_struct.rest().first().as_atom())
        if derivation_record is None:
            raise ValueError(f"This DID {launcher_id} does not belong to the connected wallet")
```
