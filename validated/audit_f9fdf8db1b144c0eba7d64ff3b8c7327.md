The vulnerability claim is real. Let me trace it precisely.

## Core Issue: Missing Lower-Bound Guard in `buyToken0InBinSpecifiedOut`

**`calculateBinPositionAtPrice`** documents a precondition that `priceX64 ∈ [lowerPriceX64, upperPriceX64]`, but this is never enforced by callers: [1](#0-0) 

The function runs entirely in `unchecked`. If `priceX64 < lowerPriceX64`, the subtraction `(priceX64 - lowerPriceX64)` wraps to a value near `type(uint256).max`, and the product with `MAX_POS_BIN` wraps again to a large but non-zero value — producing a `finalBinPosAtPriceLimit` far above `MAX_POS_BIN`.

**`buyToken0InBinSpecifiedOut`** only guards with `priceLimitX64 < upperPriceX64`, not `priceLimitX64 >= lowerPriceX64`: [2](#0-1) 

When `priceLimitX64 < lowerPriceX64 < upperPriceX64`:
1. Line 383 condition is **TRUE** → `calculateBinPositionAtPrice` is called
2. `(priceLimitX64 - lowerPriceX64)` underflows in `unchecked` → `finalBinPosAtPriceLimit` ≫ `MAX_POS_BIN`
3. Line 387: `finalBinPosAtPriceLimit < finalBinPos` is **FALSE** (since `finalBinPos ≤ MAX_POS_BIN = type(uint104).max`)
4. The price-limit branch is silently skipped — the swap executes at full depth as if no limit was set

**Contrast with `buyToken1InBinSpecifiedIn`**, which correctly handles the analogous case with an explicit lower-bound guard before calling `calculateBinPositionAtPrice`: [3](#0-2) 

`buyToken0InBinSpecifiedOut` has no equivalent guard.

## Impact Assessment

The price limit is the trader's slippage protection. When `priceLimitX64 < lowerPriceX64` (e.g., market moved up between submission and execution), the swap should return zero output. Instead it executes fully, delivering token0 to the trader at a price above their stated limit. This is **bad-price execution** — the trader pays more token1 than their price limit permits, with no revert or clamping.

---

### Title
`buyToken0InBinSpecifiedOut` silently ignores `priceLimitX64` when it falls below the current bin's lower bound, bypassing slippage protection — (`metric-core/contracts/libraries/SwapMath.sol`)

### Summary
`buyToken0InBinSpecifiedOut` calls `calculateBinPositionAtPrice` with `priceLimitX64` whenever `priceLimitX64 < upperPriceX64`, without checking `priceLimitX64 >= lowerPriceX64`. When the limit is below the bin's lower bound, the unchecked subtraction underflows, producing a `finalBinPosAtPriceLimit` far above `MAX_POS_BIN`. The subsequent comparison `finalBinPosAtPriceLimit < finalBinPos` is always false, so the price-limit branch is never taken and the swap executes without any price cap.

### Finding Description
In `SwapMath.buyToken0InBinSpecifiedOut` (line 383), the guard is:

```solidity
if (priceLimitX64 < upperPriceX64) {
    uint256 finalBinPosAtPriceLimit =
        calculateBinPositionAtPrice(lowerPriceX64, upperPriceX64, priceLimitX64, Math.Rounding.Floor);
    if (finalBinPosAtPriceLimit < finalBinPos) { ... }
}
```

`calculateBinPositionAtPrice` is `unchecked` and computes `(priceX64 - lowerPriceX64) * MAX_POS_BIN`. When `priceX64 < lowerPriceX64`, this underflows to ≈ `type(uint256).max * MAX_POS_BIN mod 2^256`, a value far above `type(uint104).max`. The `< finalBinPos` comparison is always false, so the price limit is never applied. The swap proceeds at full depth.

The analogous `buyToken1InBinSpecifiedIn` correctly handles this with:
```solidity
if (priceLimitX64 <= lowerPriceX64) {
    minFinalBinPos = 0;
} else {
    minFinalBinPos = calculateBinPositionAtPrice(...);
}
```
No such guard exists in `buyToken0InBinSpecifiedOut`.

### Impact Explanation
A trader submitting an exact-output buy-token0 swap with a price limit that falls below the current bin's lower bound (e.g., due to price movement between submission and execution) will have their slippage protection silently bypassed. The swap executes at the full market price, causing the trader to pay more token1 than their stated limit permits. This is bad-price execution meeting the Sherlock Medium threshold.

### Likelihood Explanation
Any user can supply an arbitrary `priceLimitX64`. The scenario where `priceLimitX64 < lowerPriceX64` arises naturally when the market price rises between transaction submission and on-chain execution (front-running, congestion, or normal price movement). No privileged access or malicious setup is required.

### Recommendation
Add a lower-bound guard before calling `calculateBinPositionAtPrice`, mirroring the pattern in `buyToken1InBinSpecifiedIn`:

```solidity
if (priceLimitX64 < upperPriceX64) {
    uint256 finalBinPosAtPriceLimit;
    if (priceLimitX64 <= lowerPriceX64) {
        // Price limit is at or below the bin's lower bound:
        // no position in this bin is acceptable; output zero.
        finalBinPosAtPriceLimit = 0;
    } else {
        finalBinPosAtPriceLimit =
            calculateBinPositionAtPrice(lowerPriceX64, upperPriceX64, priceLimitX64, Math.Rounding.Floor);
    }
    if (finalBinPosAtPriceLimit < finalBinPos) {
        finalBinPos = finalBinPosAtPriceLimit;
        ...
    }
}
```

When `priceLimitX64 <= lowerPriceX64`, `finalBinPosAtPriceLimit = 0 < finalBinPos` (since `currBinPos >= 0` and `finalBinPos > currBinPos` for a non-trivial swap), so `amountOutScaled` becomes 0 and the function returns `(currBinPos, 0, 0, 0)` — correctly halting the swap.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;
import "forge-std/Test.sol";
import {SwapMath} from "../contracts/libraries/SwapMath.sol";
import {BinState} from "../contracts/libraries/BinState.sol";

contract PriceLimitUnderflowPoC is Test {
    function test_priceLimitBelowLower_bypassesSlippage() public pure {
        uint128 lowerPriceX64 = 1e18;
        uint128 upperPriceX64 = 2e18;
        // Trader sets limit 1 below the bin's lower bound
        uint128 priceLimitX64 = lowerPriceX64 - 1;

        BinState memory binState = BinState({
            token0BalanceScaled: 1e18,
            token1BalanceScaled: 0,
            lengthE6: 100,
            addFeeBuyE6: 0,
            addFeeSellE6: 0
        });
        SwapMath.SwapState memory state = SwapMath.SwapState({
            amountSpecifiedRemainingScaled: 5e17, // want 0.5 token0
            amountCalculatedScaled: 0,
            protocolFeeAmountScaled: 0,
            feeExclusiveInputScaled: 0
        });

        (uint256 finalBinPos, int256 delta0Scaled,,) =
            SwapMath.buyToken0InBinSpecifiedOut(
                binState, 0, state, 0, lowerPriceX64, upperPriceX64, priceLimitX64, 0
            );

        // Expected: swap should return 0 output (price limit below current price)
        // Actual: swap executes, delta0Scaled != 0 — price limit bypassed
        assertEq(delta0Scaled, 0, "Price limit should have halted the swap");
    }
}
```

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L116-124)
```text
    unchecked {
      // Numerator ≤ 2^128 × 2^104 = 2^232; denominator > 0 by assumption.
      // Result ≤ MAX_POS_BIN since priceX64 ∈ [lower, upper].
      if (rounding == Math.Rounding.Floor) {
        return ((priceX64 - lowerPriceX64) * MAX_POS_BIN) / (upperPriceX64 - lowerPriceX64);
      } else {
        return Math.ceilDiv((priceX64 - lowerPriceX64) * MAX_POS_BIN, upperPriceX64 - lowerPriceX64);
      }
    }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L383-393)
```text
      if (priceLimitX64 < upperPriceX64) {
        uint256 finalBinPosAtPriceLimit =
          calculateBinPositionAtPrice(lowerPriceX64, upperPriceX64, priceLimitX64, Math.Rounding.Floor);

        if (finalBinPosAtPriceLimit < finalBinPos) {
          finalBinPos = finalBinPosAtPriceLimit;
          uint256 amountOutHelper =
            calculateOutputToken0FromBinPosition(binState.token0BalanceScaled, currBinPos, finalBinPos);
          amountOutScaled = amountOutHelper < amountOutScaled ? amountOutHelper : amountOutScaled;
        }
      }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L695-702)
```text
      if (priceLimitX64 <= lowerPriceX64) {
        minFinalBinPos = 0;
      } else {
        minFinalBinPos = calculateBinPositionAtPrice(lowerPriceX64, upperPriceX64, priceLimitX64, Math.Rounding.Ceil);
        if (currBinPos <= minFinalBinPos) {
          return (currBinPos, 0, 0, 0, 0);
        }
      }
```
