### Title
NFT Royalty Systematically Underpaid via Double Integer Truncation in `compute_royalty_amount` - (File: `chia/wallet/nft_wallet/nft_wallet.py`)

### Summary
`compute_royalty_amount` performs two sequential integer floor divisions when computing the royalty owed to an NFT creator during offer settlement. Both truncations round down in the buyer's favor, causing the royalty recipient to receive less than their entitled percentage on every trade. The truncated amount is what gets committed on-chain in the spend bundle, so the underpayment is enforced by the protocol itself.

### Finding Description

`compute_royalty_amount` computes the royalty as:

```python
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
``` [1](#0-0) 

There are two sequential floor divisions:

1. `abs(offered_amount) // royalty_split` — truncates the per-NFT share of the offered amount
2. `* percentage // MAX_ROYALTY_BASIS_POINTS` — truncates the royalty basis-point calculation

The mathematically correct single-truncation form would be:

```python
amount = abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)
```

The same double-truncation pattern also appears in `royalty_calculation`:

```python
"amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
``` [2](#0-1) 

And in `make_nft1_offer` when computing `trade_prices` per offered NFT:

```python
trade_prices.append((uint64(amount // offer_side_royalty_split), settlement_ph))
``` [3](#0-2) 

The result of `compute_royalty_amount` is directly used to construct the `CreateCoin` condition for the royalty payment in the spend bundle:

```python
extra_royalty_amount = compute_royalty_amount(amount, request_side_royalty_split, percentage)
payment_list.append((launcher_id, CreateCoin(address, extra_royalty_amount, [address])))
``` [4](#0-3) 

The existing test suite explicitly accepts and documents the truncation-to-zero behavior:

```python
def test_small_amount_truncates_to_zero() -> None:
    result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
    assert result == uint64(0)
``` [5](#0-4) 

### Impact Explanation

The truncated royalty amount is committed into the offer's spend bundle as a `CreateCoin` condition. The on-chain CLVM puzzle enforces exactly this truncated amount — so the underpayment is protocol-enforced, not merely a display error. NFT royalty recipients (creators) receive less than their entitled percentage on every trade where `offered_amount` is not perfectly divisible by `royalty_split * MAX_ROYALTY_BASIS_POINTS`. The maximum underpayment per NFT per trade is bounded by approximately `(royalty_split - 1) + 9999` mojos. With `royalty_split = N` (N NFTs requested in one offer), the total underpayment across all royalty recipients is up to `N × (N + 9999)` mojos. This is an accounting change affecting XCH/CAT settlement in NFT offers.

### Likelihood Explanation

Every NFT offer involving amounts that do not divide evenly triggers this truncation. An unprivileged buyer can deliberately structure offers with many NFTs or specific amounts to maximize the truncation benefit. No special access, leaked keys, or admin privileges are required — any wallet user constructing an NFT offer is affected.

### Recommendation

Replace the double-truncation with a single truncation at the end:

```python
amount = abs(offered_amount) * percentage // (royalty_split * MAX_ROYALTY_BASIS_POINTS)
```

Apply the same fix to `royalty_calculation` at line 719 and the `trade_prices` computation at line 768.

### Proof of Concept

```python
from chia.wallet.nft_wallet.nft_wallet import compute_royalty_amount

offered_amount = -10001
royalty_split = 3
percentage = 9999  # 99.99%

# Current (double truncation):
actual = compute_royalty_amount(offered_amount, royalty_split, percentage)
# Step 1: 10001 // 3 = 3333  (loses 2 mojos)
# Step 2: 3333 * 9999 // 10000 = 33326667 // 10000 = 3332
print(actual)  # 3332

# Correct (single truncation):
correct = abs(offered_amount) * percentage // (royalty_split * 10000)
# 10001 * 9999 // 30000 = 99999999 // 30000 = 3333
print(correct)  # 3333

# Royalty recipient is underpaid by 1 mojo per NFT per trade.
# With N=10 NFTs and many trades, this accumulates systematically.
```

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L797-800)
```python
                for launcher_id, address, percentage in required_royalty_info:
                    extra_royalty_amount = compute_royalty_amount(amount, request_side_royalty_split, percentage)
                    payment_list.append((launcher_id, CreateCoin(address, extra_royalty_amount, [address])))
                royalty_payments[asset] = payment_list
```

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L42-44)
```python
def test_small_amount_truncates_to_zero() -> None:
    result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
    assert result == uint64(0)
```
