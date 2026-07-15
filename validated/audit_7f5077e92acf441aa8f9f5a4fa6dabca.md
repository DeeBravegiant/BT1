### Title
NFT Royalty Bypass via Integer Truncation to Zero in Offer Settlement - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

### Summary

`compute_royalty_amount` in `chia/wallet/nft_wallet/nft_wallet.py` uses sequential integer floor-division that can produce `0` for small fungible amounts or high `royalty_split` values. When the computed royalty is zero, the offer-settlement code skips the royalty coin entirely and also strips the corresponding entry from the on-chain `trade_prices_list`, so the CLVM transfer program never enforces payment. An unprivileged taker can therefore acquire a royalty-enabled NFT without paying the creator's royalty.

### Finding Description

`compute_royalty_amount` computes the royalty as:

```python
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
``` [1](#0-0) 

Both `//` operations truncate. For example, with `offered_amount = -50`, `royalty_split = 1`, `percentage = 100` (1 %):

```
50 // 1 * 100 // 10000  →  50 * 100 // 10000  →  5000 // 10000  →  0
```

The project's own test suite explicitly documents and accepts this truncation:

```python
def test_small_amount_truncates_to_zero() -> None:
    result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
    assert result == uint64(0)
``` [2](#0-1) 

When `compute_royalty_amount` returns `0`, two downstream effects eliminate royalty enforcement entirely:

**1. Royalty coin skipped in `make_nft1_offer`:**

```python
if sum(p.amount for _, p in payments) == 0:
    continue
``` [3](#0-2) 

No royalty `CreateCoin` condition is emitted, so the NFT creator receives nothing.

**2. On-chain `trade_prices_list` entry stripped:**

```python
trade_prices_list=[
    list(price)
    for price in trade_prices
    if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
],
``` [4](#0-3) 

The CLVM royalty-transfer program enforces royalties only against entries in `trade_prices_list`. Stripping the entry means the on-chain puzzle imposes no royalty obligation, so the spend is valid without any royalty payment.

The same truncation exists in `royalty_calculation`:

```python
"amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
``` [5](#0-4) 

### Impact Explanation

An attacker (taker) can settle an NFT offer and acquire the NFT without paying the creator's royalty. Because the bypass is per-transaction, the attacker can split any large purchase into many small sub-transactions, each with a fungible amount small enough to truncate the royalty to zero, bypassing royalties on arbitrarily large total trades. This is an unauthorized accounting change affecting NFT offer settlement — the NFT creator's royalty entitlement is silently zeroed out.

### Likelihood Explanation

The threshold for truncation is low. For a 1 % royalty (`percentage = 100`), any fungible amount below 100 mojos per NFT in the split produces zero royalty. For a 0.01 % royalty (`percentage = 1`), any amount below 10,000 mojos truncates to zero. An attacker needs only to craft or respond to offers with sufficiently small per-transaction amounts, which is entirely within the control of an unprivileged user.

### Recommendation

- In `compute_royalty_amount`, reorder the arithmetic to avoid premature truncation: compute `abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)` so the multiplication happens before any division.
- Add a guard: if `percentage > 0` and the computed royalty is `0`, either revert/raise or round up to `1` mojo, preventing zero-royalty settlement for non-zero royalty NFTs.
- Remove the `if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0` filter, or replace it with a check that uses the corrected arithmetic.

### Proof of Concept

1. Alice mints an NFT with `royalty_percentage = 100` (1 %).
2. Bob creates an offer: offer 50 mojos XCH, request Alice's NFT.
3. Alice (or any taker) calls `respond_to_offer`. Internally, `compute_royalty_amount(-50, 1, 100)` returns `0`.
4. The royalty coin spend is skipped (`payment_sum == 0 → continue`).
5. The `trade_prices_list` entry is stripped because `0 * 100 // 10000 == 0`.
6. The spend bundle is submitted and accepted on-chain. Alice's NFT transfers to Bob; Alice receives 50 mojos and **zero royalty**.
7. Bob repeats with many 50-mojo sub-offers to acquire the NFT at any desired total price with no royalty paid.

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L719-719)
```python
                        "amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L893-897)
```python
                            trade_prices_list=[
                                list(price)
                                for price in trade_prices
                                if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
                            ],
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L911-912)
```python
                    if sum(p.amount for _, p in payments) == 0:
                        continue
```

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L42-44)
```python
def test_small_amount_truncates_to_zero() -> None:
    result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
    assert result == uint64(0)
```
