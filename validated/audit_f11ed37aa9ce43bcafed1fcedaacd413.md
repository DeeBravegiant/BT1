### Title
Integer Floor Division Rounding in `compute_royalty_amount` Allows NFT Royalty Bypass in Offer Settlement - (File: chia/wallet/nft_wallet/nft_wallet.py)

### Summary
The `compute_royalty_amount` function in `chia/wallet/nft_wallet/nft_wallet.py` uses two sequential integer floor divisions that can silently produce a zero royalty amount. When this occurs, `make_nft1_offer` skips the royalty coin spend entirely, allowing an NFT to be transferred without paying any royalty to the creator. An unprivileged attacker (NFT buyer) can deliberately craft an offer with a small enough fungible amount to trigger this rounding, bypassing the royalty mechanism and causing direct financial loss to the royalty recipient.

### Finding Description

`compute_royalty_amount` performs two sequential integer floor divisions:

```python
# chia/wallet/nft_wallet/nft_wallet.py, line 64
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
``` [1](#0-0) 

`MAX_ROYALTY_BASIS_POINTS` is 10000. The first division `abs(offered_amount) // royalty_split` truncates, and the second `* percentage // 10000` truncates again. For any combination where `abs(offered_amount) * percentage < 10000`, the result is zero. The only guard present checks `if royalty >= abs(offered_amount)` — it does **not** check for zero when `percentage > 0`.

The same double-division pattern appears in the static `royalty_calculation` method used by the `nft_calculate_royalties` RPC endpoint:

```python
# chia/wallet/nft_wallet/nft_wallet.py, line 719
"amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
``` [2](#0-1) 

The existing test `test_small_amount_truncates_to_zero` explicitly confirms this behavior is accepted:

```python
result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
assert result == uint64(0)
``` [3](#0-2) 

In `make_nft1_offer`, when `compute_royalty_amount` returns 0 for all NFTs in the offer, the royalty coin spend is skipped entirely:

```python
# chia/wallet/nft_wallet/nft_wallet.py, lines 910-912
payments = royalty_payments[asset] if asset in royalty_payments else []
if sum(p.amount for _, p in payments) == 0:
    continue
``` [4](#0-3) 

Additionally, the `trade_prices_list` embedded in the NFT puzzle (which enforces royalties on-chain) is also filtered to exclude zero-royalty entries:

```python
# chia/wallet/nft_wallet/nft_wallet.py, lines 893-897
trade_prices_list=[
    list(price)
    for price in trade_prices
    if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
],
``` [5](#0-4) 

This means when rounding produces zero, the on-chain NFT puzzle also receives no trade price enforcement, so the royalty bypass is complete at the CLVM level.

### Impact Explanation

An NFT buyer can craft an offer with a fungible amount small enough that `compute_royalty_amount` returns 0. The royalty recipient (NFT creator) receives zero mojos instead of the expected royalty. The NFT ownership singleton is transferred and the royalty address receives nothing. This is a direct, permanent loss of XCH or CAT tokens owed to the royalty recipient, constituting unauthorized payout redirection and corruption of offer/trade settlement state.

### Likelihood Explanation

Any unprivileged user can construct an NFT offer. The threshold for triggering zero royalty is `offered_amount * percentage < 10000`. For a 1% royalty (100 basis points), any offer under 100 mojos triggers zero royalty. For a 0.01% royalty (1 basis point), any offer under 10,000 mojos triggers zero royalty. Since CAT mojos are denominated at 1000 per CAT unit, small CAT offers are a realistic attack vector. The attacker simply needs the NFT seller to accept the crafted offer.

### Recommendation

1. In `compute_royalty_amount`, add a check that rejects a zero royalty when `percentage > 0`:
   ```python
   if royalty == 0 and percentage > 0:
       raise ValueError("Royalty rounds to zero; increase offered amount or royalty percentage")
   ```
2. Apply the same guard in `royalty_calculation` to prevent misleading RPC responses.
3. Consider reordering the arithmetic to multiply before dividing: `abs(offered_amount) * percentage // royalty_split // MAX_ROYALTY_BASIS_POINTS` to reduce precision loss.

### Proof of Concept

- NFT has a 1% royalty (100 basis points, `percentage=100`).
- Attacker crafts an offer of 99 mojos of XCH for the NFT (`offered_amount=-99`).
- `compute_royalty_amount(-99, 1, 100)` → `99 // 1 * 100 // 10000` → `9900 // 10000` → `0`.
- `royalty_payments[asset]` contains a `CreateCoin` with `amount=0`.
- `sum(p.amount for _, p in payments) == 0` → `continue` skips the royalty spend.
- The NFT singleton is transferred to the buyer; the royalty address receives 0 mojos.
- The royalty recipient permanently loses the royalty they are owed. [1](#0-0) [6](#0-5) [7](#0-6)

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L793-800)
```python
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
