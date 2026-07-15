### Title
NFT Royalty Silently Zeroed by Integer Division Truncation, Bypassing On-Chain Enforcement — (`File: chia/wallet/nft_wallet/nft_wallet.py`)

### Summary

`compute_royalty_amount` uses two sequential integer-division operations that can silently produce `0`. When this happens, `make_nft1_offer` both omits the royalty `CreateCoin` output **and** strips the corresponding entry from the NFT's on-chain `trade_prices_list`, so the CLVM transfer-program puzzle never enforces royalty payment. The NFT is transferred and the offer settles, but the royalty creator receives nothing.

### Finding Description

`compute_royalty_amount` computes the royalty owed to an NFT creator:

```python
# chia/wallet/nft_wallet/nft_wallet.py  line 64
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
```

Two integer-floor divisions are chained. The first divides by `royalty_split` (the number of NFTs on the same side of the offer); the second divides by `10000`. Either step can truncate the result to `0`. [1](#0-0) 

The own test suite already documents this: `test_small_amount_truncates_to_zero` asserts that `compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)` returns `uint64(0)`. [2](#0-1) 

Inside `make_nft1_offer`, the zero result propagates in two independent ways:

**Path 1 — off-chain royalty coin skipped.** The royalty `CreateCoin` is built with `amount=0` and placed in `royalty_payments`. Later, the code checks the total and silently skips the entire royalty spend:

```python
# line 910-912
payments = royalty_payments[asset] if asset in royalty_payments else []
if sum(p.amount for _, p in payments) == 0:
    continue          # royalty creator receives nothing
``` [3](#0-2) 

**Path 2 — on-chain `trade_prices_list` entry removed.** When the offered asset is the NFT itself, the `trade_prices_list` embedded in the NFT spend is filtered:

```python
# line 893-897
trade_prices_list=[
    list(price)
    for price in trade_prices
    if price[0] * offered_royalty_percentages[asset] // MAX_ROYALTY_BASIS_POINTS != 0
],
``` [4](#0-3) 

When the royalty rounds to zero, the entry is excluded. The NFT transfer-program puzzle (CLVM) receives an empty `trade_prices_list` and therefore imposes **no royalty obligation** on the taker. The offer settles on-chain with zero royalty enforced. [5](#0-4) 

### Impact Explanation

The NFT royalty mechanism is completely bypassed. The royalty creator is entitled to a percentage of every secondary sale but receives 0 mojos. The NFT ownership transfer is finalized on-chain with no recourse. This constitutes unauthorized reward diversion and corrupted offer settlement affecting NFTs — matching the Critical impact tier.

### Likelihood Explanation

Any unprivileged user can craft an offer. Two realistic trigger conditions exist:

1. **Small offered amount.** With a 1 % royalty (100 basis points) and `royalty_split=1`, any offered amount ≤ 99 mojos causes `99 * 100 // 10000 = 0`. The NFT owner may accept a low-ball offer without inspecting the royalty calculation.

2. **Many NFTs bundled.** `request_side_royalty_split` equals the count of NFTs requested. With 10 NFTs and 999 mojos offered: `999 // 10 * 100 // 10000 = 99 * 100 // 10000 = 0`. An attacker bundles enough NFTs to force the per-NFT royalty to zero even at a seemingly reasonable total price. [6](#0-5) 

### Recommendation

1. **Reject offers where any royalty rounds to zero.** After calling `compute_royalty_amount`, raise a `ValueError` if the result is `0` but the percentage is non-zero, preventing the offer from being constructed.

2. **Round up instead of truncating.** Use ceiling division for the royalty:
   ```python
   amount = (abs(offered_amount) * percentage + MAX_ROYALTY_BASIS_POINTS - 1) \
            // (royalty_split * MAX_ROYALTY_BASIS_POINTS)
   ```
   This guarantees at least 1 mojo is paid whenever the percentage is non-zero.

3. **Enforce a minimum offered amount.** Before building the offer, verify that `offered_amount // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS >= 1` for every royalty-bearing NFT in the bundle.

### Proof of Concept

```python
from chia.wallet.nft_wallet.nft_wallet import compute_royalty_amount, MAX_ROYALTY_BASIS_POINTS
from chia_rs.sized_ints import uint64

# Single NFT, 1% royalty, 99-mojo offer
royalty = compute_royalty_amount(offered_amount=-99, royalty_split=1, percentage=100)
assert royalty == uint64(0)   # royalty creator receives nothing

# 10 NFTs bundled, 1% royalty each, 999-mojo total offer
royalty_per_nft = compute_royalty_amount(offered_amount=-999, royalty_split=10, percentage=100)
assert royalty_per_nft == uint64(0)  # all 10 royalty creators receive nothing
```

In both cases `make_nft1_offer` will:
- skip the royalty `CreateCoin` spend (line 911–912)
- produce an empty `trade_prices_list` in the NFT spend (line 893–897)

The resulting `SpendBundle` is valid and will be accepted by the full node, transferring the NFT(s) with zero royalty paid.

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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L754-800)
```python
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
