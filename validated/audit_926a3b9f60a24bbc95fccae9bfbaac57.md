### Title
NFT Royalty Bypass via Integer Division Truncation to Zero in `compute_royalty_amount` - (File: chia/wallet/nft_wallet/nft_wallet.py)

### Summary

`compute_royalty_amount` in `chia/wallet/nft_wallet/nft_wallet.py` uses sequential integer floor-division that silently truncates the royalty to zero when the offered fungible amount is below a threshold determined by the royalty percentage. When the computed royalty is zero, `make_nft1_offer` omits the royalty payment entirely from the offer bundle and strips the corresponding `trade_prices_list` entry from the NFT spend, bypassing on-chain royalty enforcement. An unprivileged buyer can deliberately craft an offer whose fungible amount falls below this threshold, causing the NFT creator's royalty to be diverted to zero without the creator's consent.

### Finding Description

**Root cause — `compute_royalty_amount`:**

```python
# chia/wallet/nft_wallet/nft_wallet.py, line 64
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
```

`MAX_ROYALTY_BASIS_POINTS = 10000`. The expression evaluates as:

```
floor(floor(abs(offered_amount) / royalty_split) * percentage / 10000)
```

When `abs(offered_amount) * percentage < 10000`, the result is `0`. The threshold is:

```
offered_amount < 10000 / percentage  (in mojos)
```

Examples:
- 1 % royalty (100 bps): any offer < 100 mojos → royalty = 0
- 10 % royalty (1000 bps): any offer < 10 mojos → royalty = 0

The project's own test explicitly documents this:

```python
# chia/_tests/wallet/nft_wallet/test_nft_royalty.py, line 42-44
def test_small_amount_truncates_to_zero() -> None:
    result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
    assert result == uint64(0)
```

**Propagation into offer construction — `make_nft1_offer`:**

When `compute_royalty_amount` returns 0, two downstream guards silently drop the royalty from the offer:

1. The `trade_prices_list` entry for the offered NFT is filtered out:

```python
# chia/wallet/nft_wallet/nft_wallet.py, line 896
trade_prices_list=[
    list(price)
    for price in trade_prices
    if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
],
```

2. The royalty coin spend is skipped entirely:

```python
# chia/wallet/nft_wallet/nft_wallet.py, line 911
if sum(p.amount for _, p in payments) == 0:
    continue
```

With `trade_prices_list` empty, the NFT ownership-layer puzzle receives no trade-price constraint and therefore enforces no royalty payment on settlement. The offer is valid on-chain with zero royalties.

**Same truncation in `royalty_calculation` (display path):**

```python
# chia/wallet/nft_wallet/nft_wallet.py, line 719
"amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
```

Because the display path uses the same formula, the UI also shows 0 mojos royalty, so the NFT owner accepting the offer sees a consistent (but misleading) picture.

**Inconsistency with CLI helper:**

```python
# chia/cmds/wallet_funcs.py, line 1587
royalty_amount: uint64 = uint64(amounts[0][1] * nft_royalty_percentage / 10000)
```

This CLI helper uses float division and would show a non-zero royalty for the same inputs, creating a discrepancy between CLI display and actual offer construction.

### Impact Explanation

The NFT royalty recipient (the original creator, whose address is baked into the NFT metadata) receives 0 mojos instead of the contractually expected royalty. The creator has no ability to prevent this: they are not a party to the offer acceptance. The on-chain enforcement mechanism (`trade_prices_list` in the NFT spend) is completely absent from the settled transaction. This constitutes unauthorized reward diversion affecting NFT-linked coins, qualifying as Critical under the allowed impact scope.

### Likelihood Explanation

Any unprivileged buyer can craft an offer whose fungible amount is deliberately below the truncation threshold. For a 1 % royalty NFT, an offer of 99 mojos suffices. The NFT owner may accept such an offer without realizing the creator receives nothing, especially since the wallet UI (using the same truncating formula) displays 0 mojos royalty rather than raising an error. No privileged access, key material, or cryptographic break is required.

### Recommendation

Replace sequential integer floor-division with a single multiplication-before-division to preserve precision:

```python
# Before
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS

# After
amount = abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)
```

Apply the same fix to `NFTWallet.royalty_calculation` (line 719). Additionally, add a guard in `make_nft1_offer` that rejects or warns when a non-zero royalty percentage produces a zero royalty amount, rather than silently omitting the royalty payment.

### Proof of Concept

1. Mint an NFT with royalty address `R` and royalty percentage 100 (1 %).
2. As an unprivileged buyer, call `make_nft1_offer` offering 99 mojos XCH for the NFT.
3. `compute_royalty_amount(-99, 1, 100)` → `99 // 1 * 100 // 10000` = `9900 // 10000` = **0**.
4. The `trade_prices_list` filter at line 896 drops the entry (0 * 100 // 10000 == 0).
5. The royalty coin spend is skipped at line 911 (sum of payments == 0).
6. The resulting offer bundle contains no royalty coin spend and no `trade_prices_list` constraint on the NFT spend.
7. NFT owner accepts; the NFT transfers on-chain; address `R` receives 0 mojos. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L714-721)
```python
            for name, amount in fungible_asset_dict.items():
                summary_dict[id].append(
                    {
                        "asset": name,
                        "address": address,
                        "amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
                    }
                )
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L893-899)
```python
                            trade_prices_list=[
                                list(price)
                                for price in trade_prices
                                if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
                            ],
                            extra_conditions=(*extra_conditions, *announcements_to_assert),
                        )
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L909-912)
```python
                    # Skip it if we're paying 0 royalties
                    payments = royalty_payments[asset] if asset in royalty_payments else []
                    if sum(p.amount for _, p in payments) == 0:
                        continue
```

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L42-44)
```python
def test_small_amount_truncates_to_zero() -> None:
    result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
    assert result == uint64(0)
```

**File:** chia/cmds/wallet_funcs.py (L1587-1590)
```python
    royalty_amount: uint64 = uint64(amounts[0][1] * nft_royalty_percentage / 10000)
    royalty_asset_id = amounts[0][0]
    total_amount_requested = (requested[royalty_asset_id] if amount_dict == requested else 0) + royalty_amount
    return royalty_asset_id, royalty_amount, total_amount_requested
```
