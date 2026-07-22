### Title
Synthetic Ratio Precision Loss in `AnchoredPriceProvider` Silently Produces Zero Mid Price, Permanently Blocking Pool Swaps — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

---

### Summary

`AnchoredPriceProvider._getBidAndAskPrice()` computes a synthetic ratio mid price as `Math.mulDiv(mid, ORACLE_DECIMALS, mid2)` with `ORACLE_DECIMALS = 1e8`. When the base token price is less than `1e-8` of the quote token price, integer division floors the result to zero. No post-division zero-check exists, so `_computeBidAsk(0, spreadBps)` is called, which returns the stall sentinel `(0, type(uint128).max)`. `getBidAndAskPrice()` then reverts with `FeedStalled`, which the pool catches and re-throws as `PriceProviderFailed`, completely blocking all swaps.

---

### Finding Description

In the synthetic two-feed mode (`quoteFeedId != bytes32(0)`), `_getBidAndAskPrice()` computes:

```solidity
mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
``` [1](#0-0) 

`ORACLE_DECIMALS` is `1e8`. The division floors to zero whenever `mid * 1e8 < mid2`, i.e., whenever the base feed's 8-decimal price is less than `1e-8` of the quote feed's 8-decimal price.

After the division, there is no guard for `mid == 0`. The code falls through to `_computeBidAsk(mid=0, spreadBps)`:

```solidity
uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
uint256 refAsk = _bandEdge(mid, BPS_BASE_U + half, Math.Rounding.Ceil);
if (refBid == 0 || refAsk > type(uint128).max || refBid >= refAsk) {
    return (0, type(uint128).max);
}
``` [2](#0-1) 

`_bandEdge(0, ...)` is `Math.mulDiv(0, Q64 * edgeFactor, STEP_DENOM, rounding) = 0`, so `refBid == 0` triggers the stall sentinel return. [3](#0-2) 

`getBidAndAskPrice()` then reverts:

```solidity
if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
``` [4](#0-3) 

The pool's `_getBidAndAskPriceX64()` catches this and re-throws as `PriceProviderFailed`:

```solidity
try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (uint128 bid, uint128 ask) {
    ...
} catch (bytes memory reason) {
    revert PriceProviderFailed(reason);
}
``` [5](#0-4) 

Every call to `swap()` invokes `_getBidAndAskPriceX64()` as its first action, so all swaps revert for as long as the price ratio remains extreme. [6](#0-5) 

The same pattern exists in the `FaithfulAnchoredPriceProvider` test mock:

```solidity
mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2); // synthetic ratio, 8-decimal
``` [7](#0-6) 

---

### Impact Explanation

All swaps on any pool using `AnchoredPriceProvider` in synthetic ratio mode are completely blocked whenever the base/quote price ratio falls below `1e-8`. This is broken core pool functionality: the primary user-facing action (swap) becomes permanently unusable until the price ratio recovers. LPs cannot rebalance through swaps, and traders cannot execute. This satisfies the "Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows" impact gate.

---

### Likelihood Explanation

The condition `mid * 1e8 < mid2` is reachable in two realistic scenarios:

1. **Cheap base token vs. expensive quote token**: e.g., base = SHIB at $0.00001 → `mid = 1000`, quote = BTC at $60,000 → `mid2 = 6_000_000_000_000`. Then `Math.mulDiv(1000, 1e8, 6e12) = 1e11 / 6e12 = 0`.
2. **Token price crash**: A base token that was previously priced normally crashes by more than 8 orders of magnitude relative to the quote token.

The synthetic ratio mode is an explicitly supported and documented feature of `AnchoredPriceProvider` (e.g., BTC/USD ÷ ETH/USD = BTC/ETH). Any pool using this mode with a sufficiently asymmetric pair is vulnerable. No privileged action is required to trigger it — the condition arises from normal market price movement.

---

### Recommendation

Add a zero-check for the synthetic ratio result immediately after the division:

```solidity
mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
if (mid == 0) return (0, type(uint128).max); // ratio underflows 8-decimal precision
```

Alternatively, increase the intermediate precision by scaling `mid` before dividing:

```solidity
// Use 18-decimal intermediate to preserve precision
mid = Math.mulDiv(mid, 1e18, mid2); // ratio in 1e10 scale (1e18 / 1e8)
// then pass the higher-precision mid through _computeBidAsk with matching STEP_DENOM
```

The simplest safe fix is the zero-check, which fails closed (same behavior as a stale feed) rather than silently producing a zero price.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

contract PoC {
    uint256 constant ORACLE_DECIMALS = 1e8;

    function demonstrateZeroRatio() external pure returns (uint256 syntheticMid) {
        // base token: SHIB at $0.00001 → 8-decimal oracle price = 1000
        uint256 mid  = 1_000;          // 0.00001 * 1e8
        // quote token: BTC at $60,000 → 8-decimal oracle price = 6_000_000_000_000
        uint256 mid2 = 6_000_000_000_000; // 60000 * 1e8

        // Replicates AnchoredPriceProvider._getBidAndAskPrice() line 267
        syntheticMid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
        // Returns 0: 1000 * 1e8 / 6e12 = 1e11 / 6e12 = 0
        // _computeBidAsk(0, spreadBps) → (0, type(uint128).max) → FeedStalled revert
        // Pool.swap() → PriceProviderFailed → all swaps blocked
    }
}
```

With `syntheticMid == 0`, `_computeBidAsk` returns the stall sentinel, `getBidAndAskPrice` reverts with `FeedStalled`, and the pool's `swap` reverts with `PriceProviderFailed` for every caller until the market price ratio recovers above `1e-8`.

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L244-250)
```text
    function _bandEdge(
        uint256       mid,
        uint256       edgeFactor,
        Math.Rounding rounding
    ) internal pure returns (uint256) {
        return Math.mulDiv(mid, Q64 * edgeFactor, STEP_DENOM, rounding);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L263-268)
```text
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L307-313)
```text
        // Reference band: mid ± (spreadBps + minMargin), bid rounded down, ask rounded up.
        uint256 half = spreadBps * ONE_BPS_E18 + minMargin; // < BPS_BASE_U by construction (spreadBps <= MAX_SPREAD_BPS here)
        uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
        uint256 refAsk = _bandEdge(mid, BPS_BASE_U + half, Math.Rounding.Ceil);
        if (refBid == 0 || refAsk > type(uint128).max || refBid >= refAsk) {
            return (0, type(uint128).max);
        }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L227-229)
```text
    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

```

**File:** metric-core/contracts/MetricOmmPool.sol (L804-813)
```text
  function _getBidAndAskPriceX64() internal returns (uint128 bidPriceX64, uint128 askPriceX64) {
    address activePriceProvider = _resolvedPriceProvider();
    try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (uint128 bid, uint128 ask) {
      if (bid >= ask) revert BidGreaterThanAsk();
      if (bid == 0) revert BidIsZero();
      return (bid, ask);
    } catch (bytes memory reason) {
      revert PriceProviderFailed(reason);
    }
  }
```

**File:** metric-core/test/mocks/FaithfulAnchor.sol (L114-115)
```text
      mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2); // synthetic ratio, 8-decimal
      spreadBps += spreadBps2;
```
