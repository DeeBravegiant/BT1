### Title
NFT Royalty Bypass via Integer Rounding in Multi-NFT Offer Trade Price Calculation — (`chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
In `NFTWallet.make_nft1_offer`, the per-NFT trade price embedded in the spend bundle is computed with integer floor division by `offer_side_royalty_split`. A subsequent filter then silently drops any trade price whose royalty rounds to zero. When an NFT owner offers multiple royalty-enabled NFTs against a small fungible amount, every trade price can be eliminated by this filter, producing an empty `trade_prices_list` in the `-10` (CHANGE_OWNER) condition. The CLVM transfer program generates royalty-payment assertions only from that list; an empty list produces no assertions, so the transfer settles with zero royalties paid to the NFT creator.

### Finding Description

**Step 1 — Trade price is floor-divided by the number of offered NFTs (line 768):**

```python
trade_prices.append((uint64(amount // offer_side_royalty_split), settlement_ph))
```

`amount` is the total fungible amount requested; `offer_side_royalty_split` is the count of royalty-enabled NFTs being offered. Integer division truncates the remainder. [1](#0-0) 

**Step 2 — A filter drops any trade price whose implied royalty rounds to zero (line 893–897):**

```python
trade_prices_list=[
    list(price)
    for price in trade_prices
    if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
],
```

`MAX_ROYALTY_BASIS_POINTS = 10000`. If `price[0] * royalty_percentage // 10000 == 0`, the price is excluded. [2](#0-1) 

**Concrete trigger:** Offer 2 royalty-enabled NFTs (10 % royalty = 1000 bp) requesting 9 mojos of XCH.

```
per-NFT price = 9 // 2 = 4 mojos
royalty check = 4 * 1000 // 10000 = 0  → price excluded
```

Both NFTs produce a price of 4 mojos; both are filtered out. The resulting `trade_prices_list` passed to `generate_signed_transaction` is `[]`. [3](#0-2) 

**Step 3 — Empty list propagates into the CLVM spend.** The `-10` condition carries `trade_prices_list = []`. The NFT transfer program (`NFT_TRANSFER_PROGRAM_DEFAULT`) generates `ASSERT_PUZZLE_ANNOUNCEMENT` royalty-payment assertions only for entries in that list. With an empty list it generates none, so the spend is valid on-chain with zero royalties paid. [4](#0-3) 

### Impact Explanation
The NFT creator's royalty address receives 0 mojos instead of the entitled percentage of the sale price. This is an unauthorized accounting change affecting XCH (or CAT) amounts in NFT offer settlement — a Critical-class impact under the allowed scope ("offer settlement … accounting change affecting XCH, CATs, NFTs").

### Likelihood Explanation
Any NFT owner (unprivileged) can trigger this by constructing a multi-NFT offer through the standard `make_nft1_offer` RPC path. No special keys or privileges are required. The condition is easy to satisfy: offer ≥ 2 NFTs and set the total fungible amount below `offer_side_royalty_split * (10000 / royalty_percentage)` mojos. [5](#0-4) 

### Recommendation
Replace the two-step floor-divide-then-filter pattern with a calculation that preserves the full trade price and computes the royalty check without truncating the price first:

```python
# Instead of: amount // offer_side_royalty_split
# Use ceiling division so the per-NFT price is never under-reported:
import math
per_nft_price = math.ceil(amount / offer_side_royalty_split)
```

Alternatively, compute the royalty check against the *total* fungible amount before splitting, and only split for the per-NFT price entry. The filter should also use the un-split amount to decide whether royalties are owed at all.

### Proof of Concept

1. Mint two royalty-enabled NFTs (10 % royalty).
2. Call `make_nft1_offer` offering both NFTs for 9 mojos of XCH.
3. Observe `trade_prices` = `[(4, OFFER_MOD_HASH), (4, OFFER_MOD_HASH)]`.
4. Observe filter: `4 * 1000 // 10000 = 0` → both entries dropped.
5. `trade_prices_list = []` is embedded in the `-10` condition of each NFT spend.
6. A taker accepts the offer; the spend bundle is accepted on-chain.
7. The royalty address receives 0 mojos; the taker pays only the 9-mojo offer price. [6](#0-5) [7](#0-6)

### Citations

**File:** chia/wallet/nft_wallet/nft_wallet.py (L60-68)
```python
def compute_royalty_amount(offered_amount: int, royalty_split: int, percentage: int) -> uint64:
    """Compute royalty using integer arithmetic, validating against overflow and excessive percentage."""
    if percentage > MAX_ROYALTY_BASIS_POINTS:
        raise ValueError(f"NFT royalty percentage {percentage} exceeds 100% ({MAX_ROYALTY_BASIS_POINTS} basis points)")
    amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
    royalty = uint64(amount)
    if royalty >= abs(offered_amount):
        raise ValueError("Royalty amount meets or exceeds the offered amount")
    return royalty
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L623-675)
```python
    async def generate_unsigned_spendbundle(
        self,
        payments: list[CreateCoin],
        action_scope: WalletActionScope,
        fee: uint64 = uint64(0),
        coins: set[Coin] | None = None,
        new_owner: bytes | None = None,
        new_did_inner_hash: bytes | None = None,
        trade_prices_list: Program | None = None,
        metadata_update: tuple[str, str] | None = None,
        nft_coin: NFTCoinInfo | None = None,
        extra_conditions: tuple[Condition, ...] = tuple(),
    ) -> WalletSpendBundle:
        if nft_coin is None:
            if coins is None or not len(coins) == 1:
                # Make sure the user is specifying which specific NFT coin to use
                raise ValueError("NFT spends require a single selected coin")
            elif len(payments) > 1:
                raise ValueError("NFTs can only be sent to one party")
            nft_coin = await self.nft_store.get_nft_by_coin_id(coins.pop().name())
            assert nft_coin

        coin_name = nft_coin.coin.name()
        if fee > 0:
            await self.standard_wallet.create_tandem_xch_tx(
                fee,
                action_scope,
                extra_conditions=(AssertCoinAnnouncement(asserted_id=coin_name, asserted_msg=coin_name),),
            )

        unft = UncurriedNFT.uncurry(*nft_coin.full_puzzle.uncurry())
        assert unft is not None
        if unft.supports_did:
            if new_owner is None:
                # If no new owner was specified and we're sending this to ourselves, let's not reset the DID
                derivation_record: (
                    DerivationRecord | None
                ) = await self.wallet_state_manager.puzzle_store.get_derivation_record_for_puzzle_hash(
                    payments[0].puzzle_hash
                )
                if derivation_record is not None:
                    new_owner = unft.owner_did
            extra_conditions = (
                *extra_conditions,
                UnknownCondition(
                    opcode=Program.to(-10),
                    args=[
                        Program.to(new_owner),
                        Program.to(trade_prices_list),
                        Program.to(new_did_inner_hash),
                    ],
                ),
            )
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L705-723)
```python
    @staticmethod
    def royalty_calculation(
        royalty_assets_dict: dict[Any, tuple[Any, uint16]],
        fungible_asset_dict: dict[Any, uint64],
    ) -> dict[Any, list[dict[str, Any]]]:
        summary_dict: dict[Any, list[dict[str, Any]]] = {}
        for id, royalty_info in royalty_assets_dict.items():
            address, percentage = royalty_info
            summary_dict[id] = []
            for name, amount in fungible_asset_dict.items():
                summary_dict[id].append(
                    {
                        "asset": name,
                        "address": address,
                        "amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
                    }
                )

        return summary_dict
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L753-760)
```python
        # Let's gather some information about the royalties
        offer_side_royalty_split: int = 0
        request_side_royalty_split: int = 0
        for asset, amount in royalty_nft_asset_dict.items():  # requested non fungible items
            if amount > 0:
                request_side_royalty_split += 1
            elif amount < 0:
                offer_side_royalty_split += 1
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L762-768)
```python
        trade_prices: list[tuple[uint64, bytes32]] = []
        for asset, amount in fungible_asset_dict.items():  # requested fungible items
            if amount > 0 and offer_side_royalty_split > 0:
                settlement_ph: bytes32 = (
                    OFFER_MOD_HASH if asset is None else construct_puzzle(driver_dict[asset], OFFER_MOD).get_tree_hash()
                )
                trade_prices.append((uint64(amount // offer_side_royalty_split), settlement_ph))
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L885-899)
```python
                    else:
                        assert asset is not None
                        await wallet.generate_signed_transaction(
                            [abs(amount)],
                            [OFFER_MOD_HASH],
                            inner_action_scope,
                            fee=fee_left_to_pay,
                            coins=offered_coins_by_asset[asset],
                            trade_prices_list=[
                                list(price)
                                for price in trade_prices
                                if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
                            ],
                            extra_conditions=(*extra_conditions, *announcements_to_assert),
                        )
```
