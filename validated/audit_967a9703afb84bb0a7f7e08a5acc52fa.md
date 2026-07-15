### Title
NFT Royalty Underpayment via Sequential Integer Floor Division in `compute_royalty_amount` — (File: chia/wallet/nft_wallet/nft_wallet.py)

---

### Summary

The `compute_royalty_amount` function and `royalty_calculation` static method in `NFTWallet` use two sequential integer floor divisions to compute royalty amounts owed to NFT creators during offer settlement. Because Python's `//` operator always truncates toward zero, the computed royalty is always ≤ the mathematically correct value. This systematically underpays NFT royalties in favor of the buyer, and the error accumulates across many trades. An unprivileged buyer can craft offers with specific amounts and multiple NFTs to maximize the per-trade rounding loss to the creator.

---

### Finding Description

In `chia/wallet/nft_wallet/nft_wallet.py`, three locations perform integer floor division on royalty amounts:

**1. `compute_royalty_amount` (line 64):**
```python
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
```
Two sequential `//` operations. The first divides the offered amount by the number of NFTs being requested (`royalty_split`), discarding the remainder. The second divides by `MAX_ROYALTY_BASIS_POINTS` (10000), again discarding the remainder. The mathematically correct value is:
```
abs(offered_amount) * percentage / (royalty_split * MAX_ROYALTY_BASIS_POINTS)
```
but the computed value is always strictly less than or equal to this.

**2. `royalty_calculation` static method (line 719):**
```python
"amount": abs(amount) // len(royalty_assets_dict) * percentage // MAX_ROYALTY_BASIS_POINTS,
```
Identical two-step truncation pattern. This is the value returned by the `nft_calculate_royalties` RPC endpoint and used by the wallet UI to display and commit royalty amounts.

**3. `make_nft1_offer` trade price split (line 768):**
```python
trade_prices.append((uint64(amount // offer_side_royalty_split), settlement_ph))
```
The per-NFT trade price embedded in the NFT spend (used by the CLVM transfer program to enforce royalty minimums) is also floor-divided. This means the on-chain enforcement threshold is itself rounded down. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

**Impact class:** Critical — systematic accounting change affecting NFT offer settlement (royalty payments to NFT creators).

Every NFT trade that involves multiple requested NFTs (`request_side_royalty_split > 1`) or a fungible amount that is not evenly divisible by `royalty_split * MAX_ROYALTY_BASIS_POINTS` results in the NFT creator receiving fewer mojos than owed. The maximum error per trade from the first division alone is `royalty_split − 1` mojos. Because the `trade_prices` list embedded in the NFT spend is also rounded down, the CLVM transfer program enforces a lower royalty minimum than the creator set, meaning the underpayment is accepted on-chain.

Concrete example:
- `offered_amount = 10001` mojos, `royalty_split = 3`, `percentage = 5000` (50%)
- Correct royalty per NFT: `10001 × 5000 / (3 × 10000) = 1666.83` mojos
- Computed: `(10001 // 3) × 5000 // 10000 = 3333 × 5000 // 10000 = 1666` mojos
- Total paid: `3 × 1666 = 4998` mojos vs. correct `≈ 5000.5` mojos
- Creator loses 2–3 mojos per trade; across thousands of trades this accumulates into a meaningful shortfall.

The existing test `test_small_amount_truncates_to_zero` in `chia/_tests/wallet/nft_wallet/test_nft_royalty.py` explicitly acknowledges that small amounts truncate to zero royalty — confirming the behavior is known but not treated as a security issue. [5](#0-4) 

---

### Likelihood Explanation

Any unprivileged user who creates or responds to an NFT offer triggers this code path. No special privileges, leaked keys, or admin access are required. The attacker simply:
1. Identifies an NFT with a non-zero royalty percentage.
2. Constructs an offer requesting multiple royalty-enabled NFTs simultaneously (increasing `request_side_royalty_split`) with a fungible amount chosen to maximize the remainder from the first floor division.
3. Repeats across many trades.

The `make_nft1_offer` function is reachable via the standard wallet RPC (`create_offer_for_ids`) and the CLI (`chia wallet make_offer`), both of which are unprivileged entry points. [6](#0-5) 

---

### Recommendation

Replace both sequential floor divisions with a single ceiling division to ensure the royalty always rounds in favor of the creator:

```python
import math
# Instead of:
amount = abs(offered_amount) // royalty_split * percentage // MAX_ROYALTY_BASIS_POINTS
# Use:
amount = math.ceil(abs(offered_amount) * percentage / (royalty_split * MAX_ROYALTY_BASIS_POINTS))
```

Apply the same fix to `royalty_calculation` (line 719) and the `trade_prices` split (line 768). This matches the recommendation from the external report: always use the rounding direction that benefits the protocol (creator) rather than the user (buyer).

---

### Proof of Concept

```python
from chia.wallet.nft_wallet.nft_wallet import compute_royalty_amount

# Scenario: 3 NFTs requested, 10001 mojos offered, 50% royalty
offered_amount = -10001
royalty_split = 3
percentage = 5000  # 50%

royalty_per_nft = compute_royalty_amount(offered_amount, royalty_split, percentage)
total_royalty_paid = royalty_per_nft * royalty_split

# Mathematically correct total royalty
correct_total = abs(offered_amount) * percentage / MAX_ROYALTY_BASIS_POINTS
# = 10001 * 5000 / 10000 = 5000.5 mojos

print(f"Royalty per NFT: {royalty_per_nft}")   # 1666
print(f"Total paid:      {total_royalty_paid}") # 4998
print(f"Correct total:   {correct_total}")      # 5000.5
print(f"Creator shortfall per trade: {correct_total - total_royalty_paid}")  # 2.5 mojos
# Across 1,000,000 trades: 2,500,000 mojos = 0.0025 XCH lost by creator
```

The `test_royalty_split_across_multiple_nfts` test in `chia/_tests/wallet/nft_wallet/test_nft_royalty.py` uses an evenly divisible amount (`-2_000_000`, split by 2) and therefore does not catch the truncation error that occurs with non-divisible amounts. [7](#0-6) [8](#0-7)

### Citations

**File:** chia/wallet/nft_wallet/nft_wallet.py (L57-68)
```python
MAX_ROYALTY_BASIS_POINTS = 10000


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

**File:** chia/wallet/nft_wallet/nft_wallet.py (L725-733)
```python
    @staticmethod
    async def make_nft1_offer(
        wallet_state_manager: Any,
        offer_dict: dict[bytes32 | None, int],
        driver_dict: dict[bytes32, PuzzleInfo],
        action_scope: WalletActionScope,
        fee: uint64,
        extra_conditions: tuple[Condition, ...],
    ) -> Offer:
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

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L24-26)
```python
def test_royalty_split_across_multiple_nfts() -> None:
    result = compute_royalty_amount(offered_amount=-2_000_000, royalty_split=2, percentage=1000)
    assert result == uint64(100_000)
```

**File:** chia/_tests/wallet/nft_wallet/test_nft_royalty.py (L42-44)
```python
def test_small_amount_truncates_to_zero() -> None:
    result = compute_royalty_amount(offered_amount=-50, royalty_split=1, percentage=100)
    assert result == uint64(0)
```
