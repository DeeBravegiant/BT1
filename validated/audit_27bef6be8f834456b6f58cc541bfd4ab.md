### Title
NFT Royalty Dilution via Equal Price-Split Across Bundled NFTs in Multi-NFT Offers — (File: chia/wallet/nft_wallet/nft_wallet.py)

---

### Summary

`NFTWallet.make_nft1_offer` divides the total fungible payment equally across all NFTs in an offer when computing `trade_prices` (the on-chain price list that drives royalty enforcement). An unprivileged attacker who owns a royalty-bearing NFT can bundle it with an arbitrary number of self-minted, zero-royalty NFTs in a single offer, causing the legitimate NFT creator's royalty to be computed against `total_price / N` instead of `total_price`, cheating the creator out of the difference.

---

### Finding Description

In `make_nft1_offer`, the code counts every NFT on the offer side into `offer_side_royalty_split` and then computes `trade_prices` as:

```python
trade_prices.append((uint64(amount // offer_side_royalty_split), settlement_ph))
``` [1](#0-0) 

This single price entry — `total_fungible // N` — is passed as `trade_prices_list` to every NFT being offered:

```python
trade_prices_list=[
    list(price)
    for price in trade_prices
    if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
],
``` [2](#0-1) 

The NFT ownership-layer puzzle uses `trade_prices_list` to compute and enforce the required royalty payment on-chain. Because the price is divided equally by the count of all bundled NFTs — including worthless, zero-royalty ones — the royalty for the legitimate NFT is proportionally reduced.

The same equal-division flaw appears in the display-only `royalty_calculation` helper:

```python
"amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
``` [3](#0-2) 

and in `compute_royalty_amount` called for the request-side path:

```python
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
``` [4](#0-3) 

where `royalty_split` is `request_side_royalty_split` — again the raw count of all requested NFTs. [5](#0-4) 

---

### Impact Explanation

The NFT ownership-layer puzzle enforces royalties based on the `trade_prices_list` supplied in the spend solution. By diluting that list, the attacker causes the on-chain puzzle to accept a spend that pays the royalty creator a fraction of the contractually expected amount. This is a direct, on-chain accounting change affecting NFT royalty coins — an unauthorized diversion of royalty payments away from the NFT creator.

**Concrete example**: NFT_A carries a 10 % royalty. Attacker bundles it with 9 self-minted zero-royalty NFTs and lists all 10 for 1 000 XCH. `trade_prices` becomes `[(100, settlement_ph)]`. The NFT_A puzzle enforces royalty on 100 XCH → 10 XCH paid, instead of the expected 100 XCH. The creator is cheated of 90 XCH per sale.

---

### Likelihood Explanation

The attacker only needs to:
1. Own the NFT they wish to sell (normal seller).
2. Mint an arbitrary number of zero-royalty NFTs (permissionless, low cost).
3. Construct a multi-NFT offer via the standard wallet API.

No privileged access, leaked keys, or cryptographic breaks are required. The attack is repeatable on every sale.

---

### Recommendation

Royalties must be computed per-NFT based on that NFT's individual attributed price, not on `total_fungible / count`. Options:

- Require each NFT in a bundle to carry an explicit, independently negotiated price, and compute royalties against that price.
- Restrict royalty-bearing NFT offers to one NFT per offer (analogous to the Kairos fix of restricting provisions to 1).
- At minimum, validate that the sum of all per-NFT prices equals the total fungible amount before computing royalties.

---

### Proof of Concept

1. Attacker mints `NFT_A` (royalty address = creator, 10 % royalty) and `NFT_B … NFT_N` (0 % royalty, owned by attacker).
2. Attacker calls `make_nft1_offer` with `offer_dict = {NFT_A: -1, NFT_B: -1, …, NFT_N: -1, XCH: +1000}`.
3. Inside `make_nft1_offer`, `offer_side_royalty_split = N`, so `trade_prices = [(1000 // N, settlement_ph)]`.
4. The spend bundle is submitted; the NFT_A ownership-layer puzzle enforces royalty = `(1000 // N) * 10 % / 100`.
5. For N = 10: creator receives 10 XCH instead of 100 XCH — a 90 % royalty reduction.
6. The taker pays 1 000 XCH total; the attacker receives NFT_A at effectively 1 % royalty cost regardless of the stated 10 % rate. [6](#0-5)

### Citations

**File:** chia/wallet/nft_wallet/nft_wallet.py (L64-64)
```python
    amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L719-719)
```python
                        "amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L753-800)
```python
        # Let's gather some information about the royalties
        offer_side_royalty_split: int = 0
        request_side_royalty_split: int = 0
        for asset, amount in royalty_nft_asset_dict.items():  # requested non fungible items
            if amount > 0:
                request_side_royalty_split += 1
            elif amount < 0:
                offer_side_royalty_split += 1

        trade_prices: list[tuple[uint64, bytes32]] = []
        for asset, amount in fungible_asset_dict.items():  # requested fungible items
            if amount > 0 and offer_side_royalty_split > 0:
                settlement_ph: bytes32 = (
                    OFFER_MOD_HASH if asset is None else construct_puzzle(driver_dict[asset], OFFER_MOD).get_tree_hash()
                )
                trade_prices.append((uint64(amount // offer_side_royalty_split), settlement_ph))

        required_royalty_info: list[tuple[bytes32, bytes32, uint16]] = []  # [(launcher_id, address, percentage)]
        offered_royalty_percentages: dict[bytes32, uint16] = {}
        for asset, amount in royalty_nft_asset_dict.items():  # royalty enabled NFTs
            transfer_info = driver_dict[asset].also().also()  # type: ignore
            assert isinstance(transfer_info, PuzzleInfo)
            royalty_percentage_raw = transfer_info["transfer_program"]["royalty_percentage"]
            assert royalty_percentage_raw is not None
            # clvm encodes large ints as bytes
            if isinstance(royalty_percentage_raw, bytes):
                royalty_percentage = int_from_bytes(royalty_percentage_raw)
            else:
                royalty_percentage = int(royalty_percentage_raw)
            if amount > 0:
                required_royalty_info.append(
                    (
                        asset,
                        bytes32(transfer_info["transfer_program"]["royalty_address"]),
                        uint16(royalty_percentage),
                    )
                )
            else:
                offered_royalty_percentages[asset] = uint16(royalty_percentage)

        royalty_payments: dict[bytes32 | None, list[tuple[bytes32, CreateCoin]]] = {}
        for asset, amount in fungible_asset_dict.items():  # offered fungible items
            if amount < 0 and request_side_royalty_split > 0:
                payment_list: list[tuple[bytes32, CreateCoin]] = []
                for launcher_id, address, percentage in required_royalty_info:
                    extra_royalty_amount = compute_royalty_amount(amount, request_side_royalty_split, percentage)
                    payment_list.append((launcher_id, CreateCoin(address, extra_royalty_amount, [address])))
                royalty_payments[asset] = payment_list
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L893-897)
```python
                            trade_prices_list=[
                                list(price)
                                for price in trade_prices
                                if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
                            ],
```
