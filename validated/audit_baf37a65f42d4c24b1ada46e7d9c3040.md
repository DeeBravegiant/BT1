### Title
NFT Royalty Integer Truncation to Zero Bypasses On-Chain Royalty Enforcement in Offer Settlement — (File: `chia/wallet/nft_wallet/nft_wallet.py`)

---

### Summary

`compute_royalty_amount` uses integer floor division that silently truncates the royalty to zero when the offered fungible amount is small relative to the 10 000-basis-point denominator. When the royalty is zero, `make_nft1_offer` filters that price out of the `trade_prices_list` passed to the NFT transfer puzzle, so the CLVM puzzle enforces **no royalty at all**. An unprivileged attacker can craft an offer whose price sits below the truncation threshold, acquire the NFT, and pay the royalty creator nothing.

---

### Finding Description

**Root cause — `compute_royalty_amount`:**

```python
# chia/wallet/nft_wallet/nft_wallet.py  line 64
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
```

Both divisions are integer floor divisions. For any `offered_amount` where `abs(offered_amount) * percentage < 10 000`, the result is `0`. The existing test explicitly documents this:

```python
# chia/_tests/wallet/nft_wallet/test_nft_royalty.py  line 42-44
def test_small_amount_truncates_to_zero() -> None:
    result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
    assert result == uint64(0)
```

Threshold examples:
- 1 % royalty (100 bp) → any offer < 100 mojos → royalty = 0
- 0.1 % royalty (10 bp) → any offer < 1 000 mojos → royalty = 0

**Propagation — `make_nft1_offer` filters zero-royalty prices from `trade_prices_list`:**

```python
# chia/wallet/nft_wallet/nft_wallet.py  lines 893-897
trade_prices_list=[
    list(price)
    for price in trade_prices
    if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
],
```

When every price in `trade_prices` produces a zero royalty, `trade_prices_list` is empty. The NFT transfer puzzle (`NFT_TRANSFER_PROGRAM_DEFAULT`) receives an empty list and therefore enforces **no royalty output condition**. The spend is valid on-chain with zero royalty paid.

**Royalty announcement guard also skips zero-amount payments:**

```python
# chia/wallet/nft_wallet/nft_wallet.py  lines 846-854
announcements_to_assert.extend([
    AssertPuzzleAnnouncement(...)
    for launcher_id, p in payments
    if p.amount > 0          # ← zero-royalty payments are silently dropped
])
```

No announcement is asserted, so the spend bundle carries no cryptographic commitment to a royalty payment.

---

### Impact Explanation

An attacker creates a taker offer to purchase a royalty-bearing NFT at a price below the truncation threshold (e.g., 99 mojos for a 1 % royalty NFT). The wallet builds a spend bundle with an empty `trade_prices_list`. The NFT transfer puzzle accepts the spend without requiring any royalty output. The royalty creator — a third party whose address is baked into the NFT puzzle — receives zero mojos. The attacker acquires the NFT and the royalty creator's contractual on-chain entitlement is silently voided. This constitutes unauthorized alteration of offer/trade settlement state affecting NFTs and their singleton-controlled royalty obligations.

---

### Likelihood Explanation

- No special privileges are required; any wallet can create an offer.
- The price threshold is low and easy to target deliberately (e.g., 99 mojos for a 1 % NFT).
- The NFT owner may accept without noticing the royalty is zero, especially if the UI `royalty_calculation` display (which uses the same truncating arithmetic at line 719) also shows `0`.
- The `royalty_calculation` RPC endpoint exposed via `nft_calculate_royalties` uses the identical truncating formula, so the pre-trade UI summary confirms "0 royalty" to both parties, masking the bypass.

---

### Recommendation

1. In `compute_royalty_amount`, raise an error (or return a minimum of 1 mojo) when `percentage > 0` but the computed royalty is 0, preventing silent bypass:
   ```python
   if percentage > 0 and amount == 0:
       raise ValueError("Royalty amount rounds to zero; increase offered amount or reduce royalty split")
   ```
2. In `make_nft1_offer`, reject or warn when a non-zero royalty percentage produces a zero `extra_royalty_amount` rather than silently omitting the price from `trade_prices_list`.
3. Apply the same fix to `royalty_calculation` so the UI and the enforcement path are consistent.

---

### Proof of Concept

1. Mint an NFT with `royalty_percentage = 100` (1 %) and a distinct `royalty_address`.
2. As an attacker, call `create_offer_for_ids` offering `99` mojos of XCH in exchange for the NFT.
3. Internally, `compute_royalty_amount(-99, 1, 100)` → `99 * 100 // 10000 = 0`.
4. The filter at line 896 removes the price; `trade_prices_list = []`.
5. The NFT transfer puzzle is solved with an empty trade-prices list → no royalty output required.
6. The NFT owner accepts; the spend is confirmed on-chain.
7. The royalty creator's address receives 0 mojos; the attacker holds the NFT. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L714-720)
```python
            for name, amount in fungible_asset_dict.items():
                summary_dict[id].append(
                    {
                        "asset": name,
                        "address": address,
                        "amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
                    }
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L846-854)
```python
            announcements_to_assert.extend(
                [
                    AssertPuzzleAnnouncement(
                        asserted_ph=royalty_ph,
                        asserted_msg=Program.to((launcher_id, [p.as_condition_args()])).get_tree_hash(),
                    )
                    for launcher_id, p in payments
                    if p.amount > 0
                ]
```

**File:** chia/wallet/nft_wallet/nft_wallet.py (L893-897)
```python
                            trade_prices_list=[
                                list(price)
                                for price in trade_prices
                                if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
                            ],
```

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L42-44)
```python
def test_small_amount_truncates_to_zero() -> None:
    result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
    assert result == uint64(0)
```
