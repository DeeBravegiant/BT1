### Title
Data Layer Mirror Coins Spendable by Anyone Due to ACS Inner Puzzle — (`File: chia/wallet/db_wallet/db_wallet_puzzles.py`, `chia/data_layer/data_layer_wallet.py`)

### Summary
The Data Layer mirror puzzle is constructed as `P2_PARENT.curry(Program.to(1))`, where `Program.to(1)` is the "anyone-can-spend" (ACS) inner puzzle. Because the inner puzzle imposes no signature requirement, any unprivileged attacker who knows a mirror coin's parent coin details (all public on-chain) can spend the mirror coin and redirect its XCH to an arbitrary address. The wallet-side ownership guard exists only in Python and is trivially bypassed by constructing the spend directly.

### Finding Description

`create_mirror_puzzle()` returns `P2_PARENT.curry(Program.to(1))`. [1](#0-0) 

`Program.to(1)` is the ACS puzzle — it returns whatever conditions the spender provides, with no key check and no signature requirement.

When `DataLayerWallet.delete_mirror` constructs the spend, it passes `G2Element()` (the identity / empty BLS signature) as the aggregate signature: [2](#0-1) 

The solution supplied to the mirror puzzle is:

```
[parent_coin.parent_coin_info, parent_inner_puzzle, parent_coin.amount, inner_sol]
```

`P2_PARENT` uses these fields to reconstruct and assert the parent coin's identity (`ASSERT_MY_PARENT_ID`), then runs the **curried** inner puzzle — which is ACS — with `inner_sol`. Because ACS simply returns whatever conditions are in `inner_sol`, the spender can emit any `CREATE_COIN` condition they like, including one that sends the mirror coin's XCH to an attacker-controlled address. No BLS signature over any key is ever required.

The only ownership check is a wallet-layer Python guard: [3](#0-2) 

This guard is enforced only inside the wallet RPC path. An attacker who constructs the spend bundle directly — bypassing the wallet — faces no on-chain enforcement whatsoever.

### Impact Explanation

Every mirror coin on-chain shares the same puzzle hash (`MIRROR_PUZZLE_HASH`). An attacker can enumerate all mirror coins via a puzzle-hash subscription, fetch each coin's parent coin (public blockchain data), and submit a spend bundle with `G2Element()` that redirects the mirror coin's XCH to an attacker address. No key material is needed. This constitutes unauthorized spend of XCH-denominated coins, matching the Critical impact tier (unauthorized spend of XCH-controlled coins).

### Likelihood Explanation

Mirror coins are publicly enumerable by puzzle hash. Parent coin details are always available on-chain. The spend requires no signature and no privileged access. Any node operator or RPC client can execute this attack.

### Recommendation

Replace the ACS inner puzzle with a puzzle that enforces ownership. The standard approach is to curry the **parent coin's inner puzzle** (e.g., the standard `p2_delegated_puzzle_or_hidden_puzzle` for the creator's key) as the mirror puzzle's inner puzzle, so that spending the mirror coin requires a valid BLS signature from the original creator — exactly as `delete_mirror` already assumes at the wallet layer.

### Proof of Concept

1. Subscribe to `MIRROR_PUZZLE_HASH` on a full node to enumerate all live mirror coins.
2. For each mirror coin `M`, fetch `M.parent_coin_info` → parent coin `P` (public).
3. Fetch `P`'s puzzle reveal (public) to obtain `parent_inner_puzzle`.
4. Construct:
   ```python
   inner_sol = self.standard_wallet.make_solution(
       primaries=[CreateCoin(attacker_puzhash, mirror_coin.amount)]
   )
   mirror_spend = make_spend(
       mirror_coin,
       create_mirror_puzzle(),
       Program.to([P.parent_coin_info, parent_inner_puzzle, P.amount, inner_sol]),
   )
   bundle = WalletSpendBundle([mirror_spend], G2Element())  # no signature
   ```
5. Push `bundle` to the mempool. It is accepted and confirmed, transferring the mirror coin's XCH to `attacker_puzhash`. [4](#0-3) [5](#0-4)

### Citations

**File:** chia/wallet/db_wallet/db_wallet_puzzles.py (L90-94)
```python
def create_mirror_puzzle() -> Program:
    return P2_PARENT.curry(Program.to(1))


MIRROR_PUZZLE_HASH = create_mirror_puzzle().get_tree_hash()
```

**File:** chia/data_layer/data_layer_wallet.py (L703-743)
```python
    async def delete_mirror(
        self,
        mirror_id: bytes32,
        peer: WSChiaConnection,
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> None:
        mirror: Mirror = await self.get_mirror(mirror_id)
        mirror_coin: Coin = (await self.wallet_state_manager.wallet_node.get_coin_state([mirror.coin_id], peer=peer))[
            0
        ].coin
        parent_coin: Coin = (
            await self.wallet_state_manager.wallet_node.get_coin_state([mirror_coin.parent_coin_info], peer=peer)
        )[0].coin
        inner_puzzle_derivation: (
            DerivationRecord | None
        ) = await self.wallet_state_manager.puzzle_store.get_derivation_record_for_puzzle_hash(parent_coin.puzzle_hash)
        if inner_puzzle_derivation is None:
            raise ValueError(f"DL Wallet does not have permission to delete mirror with ID {mirror_id}")

        parent_inner_puzzle: Program = self.standard_wallet.puzzle_for_pk(inner_puzzle_derivation.pubkey)
        new_puzhash: bytes32 = await action_scope.get_puzzle_hash(self.wallet_state_manager)
        excess_fee: int = fee - mirror_coin.amount
        inner_sol: Program = self.standard_wallet.make_solution(
            primaries=[CreateCoin(new_puzhash, uint64(mirror_coin.amount - fee))] if excess_fee < 0 else [],
            conditions=(*extra_conditions, CreateCoinAnnouncement(b"$")) if excess_fee > 0 else extra_conditions,
        )
        mirror_spend = make_spend(
            mirror_coin,
            create_mirror_puzzle(),
            Program.to(
                [
                    parent_coin.parent_coin_info,
                    parent_inner_puzzle,
                    parent_coin.amount,
                    inner_sol,
                ]
            ),
        )
        mirror_bundle = WalletSpendBundle([mirror_spend], G2Element())
```
