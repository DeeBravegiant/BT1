Looking at the actual code at lines 758-773:

Audit Report

## Title
Fallback at `buyToken1InBinSpecifiedIn` lines 758-760 consumes full input without updating `out1Scaled`, causing trader to overpay — (`metric-core/contracts/libraries/SwapMath.sol`)

## Summary

`buyToken1InBinSpecifiedIn` contains a three-stage iterative refinement to find the correct bin position for an exact-input token0 swap. When both the analytical solution (Stage 1) and the single proportional refinement (Stage 2) still underestimate the required cost, a fallback at lines 758-760 forces `totalIn0Scaled = state.amountSpecifiedRemainingScaled` without updating `out1Scaled`. The downstream accounting then charges the trader the full remaining input while crediting only the output computed for the under-moved position, causing a direct loss of user principal.

## Finding Description

The three-stage logic in `buyToken1InBinSpecifiedIn` is:

**Stage 1 — Analytical solution** (lines 721-736): `computeAnalyticalTargetPosForSellToken0` produces an initial `targetPos`; `out1Scaled` and `totalIn0Scaled` are computed for that position. [1](#0-0) 

**Stage 2 — Single refinement** (lines 738-757): If `totalIn0Scaled < remaining && targetPos > minFinalBinPos`, the delta is scaled by `remaining/totalIn0Scaled` (ceiling), `targetPos` is moved further, and both `out1Scaled` and `totalIn0Scaled` are recomputed. [2](#0-1) 

**Stage 3 — Fallback** (lines 758-760): If after Stage 2 `totalIn0Scaled` is still less than `remaining` and `targetPos > minFinalBinPos`, the code executes:

```solidity
if (totalIn0Scaled < state.amountSpecifiedRemainingScaled && targetPos > minFinalBinPos) {
    totalIn0Scaled = state.amountSpecifiedRemainingScaled;
}
``` [3](#0-2) 

`out1Scaled` is **not updated**. It still holds the value from line 750, computed for the Stage-2 `targetPos` whose true cost is `totalIn0Scaled_before_fallback < remaining`.

The scale-down branch at lines 762-773 (which does update `out1Scaled` proportionally) does **not** fire because after the fallback `totalIn0Scaled == state.amountSpecifiedRemainingScaled`, making the condition `totalIn0Scaled > state.amountSpecifiedRemainingScaled` false. [4](#0-3) 

Final accounting then executes:

```solidity
state.amountSpecifiedRemainingScaled -= totalIn0Scaled;  // → 0 (full input consumed)
state.amountCalculatedScaled += out1Scaled;              // under-computed output
``` [5](#0-4) 

The pool receives `totalIn0Scaled` (full input) but releases only `out1Scaled` (output for a position that costs strictly less than the full input). The gap `remaining - totalIn0Scaled_before_fallback` is silently absorbed into the pool's token0 balance with no corresponding token1 release and no fee accounting.

## Impact Explanation

The trader pays `state.amountSpecifiedRemainingScaled` (full remaining input) but receives `out1Scaled` which was computed for a bin position movement that costs strictly less. The difference is a direct loss of user principal that accrues to the pool's token0 balance without any LP share issuance or fee accounting — it is not a fee, it is an accounting error. This satisfies the "wrong exact-input output amount" and "swap conservation failure" impact categories. The magnitude is `state.amountSpecifiedRemainingScaled - totalIn0Scaled_before_fallback`, which in bins with a large price spread (`upperPriceX64 >> lowerPriceX64`) and a position near the top of the bin can be a non-trivial fraction of the input.

## Likelihood Explanation

The fallback fires when: (1) the analytical quadratic approximation underestimates the required position movement, (2) the single proportional refinement step also underestimates (i.e., the price curve is concave enough that scaling the delta by `remaining/totalIn0Scaled` still lands short), and (3) `targetPos > minFinalBinPos` (the price limit has not been reached). This is reachable through a normal, unprivileged `swap` call on any pool with a sufficiently wide bin (`lengthE6` large) and a starting position near `type(uint104).max`. No trusted role, malicious setup, or non-standard token is required. The condition is deterministic and reproducible for specific `(binState, currBinPos, amountSpecifiedRemainingScaled)` tuples.

## Recommendation

After the fallback at line 759, `out1Scaled` must be updated to reflect the full input. The correct fix is to apply the same proportional rescaling used in the scale-down branch (lines 770-771) before bumping `totalIn0Scaled`:

```solidity
if (totalIn0Scaled < state.amountSpecifiedRemainingScaled && targetPos > minFinalBinPos) {
    out1Scaled = (out1Scaled * state.amountSpecifiedRemainingScaled) / totalIn0Scaled;
    totalIn0Scaled = state.amountSpecifiedRemainingScaled;
}
```

Alternatively (safer), clamp `targetPos` to `minFinalBinPos` and consume only the input required to reach it, leaving the remainder for the next bin iteration, preserving the invariant that `totalIn0Scaled` always equals the true cost for the returned `out1Scaled`.

## Proof of Concept

```solidity
function testFuzz_buyToken1SpecifiedIn_fallbackOverpay(
    uint104 currBinPos,
    uint104 token1Balance,
    uint128 remainingIn,
    uint128 lowerPriceX64,
    uint128 upperPriceX64
) public pure {
    currBinPos    = uint104(bound(currBinPos,    type(uint104).max / 2, type(uint104).max));
    token1Balance = uint104(bound(token1Balance, 1e18, type(uint104).max));
    lowerPriceX64 = uint128(bound(lowerPriceX64, 1e10, 1e18));
    upperPriceX64 = uint128(bound(upperPriceX64, lowerPriceX64 * 10, lowerPriceX64 * 1000));
    remainingIn   = uint128(bound(remainingIn,   1e18, type(uint104).max));

    BinState memory bin = BinState({
        token0BalanceScaled: 0,
        token1BalanceScaled: token1Balance,
        lengthE6: 0, addFeeBuyE6: 0, addFeeSellE6: 0
    });
    SwapMath.SwapState memory state = SwapMath.SwapState({
        amountSpecifiedRemainingScaled: remainingIn,
        amountCalculatedScaled: 0,
        protocolFeeAmountScaled: 0,
        feeExclusiveInputScaled: 0
    });

    uint256 remainingBefore = state.amountSpecifiedRemainingScaled;
    (, uint256 out1Scaled,,,) = SwapMath.buyToken1InBinSpecifiedIn(
        bin, currBinPos, state, 0, lowerPriceX64, upperPriceX64, 0, 0
    );
    uint256 consumed = remainingBefore - state.amountSpecifiedRemainingScaled;

    // Invariant: consumed should not exceed the fair price for out1Scaled at the bin curve
    if (out1Scaled > 0 && consumed > 0) {
        assertLe(
            consumed,
            Math.mulDiv(out1Scaled, invertedStartingPriceX64, SwapMath.ONE_X64) + ROUNDING_TOLERANCE,
            "Trader overpaid: consumed > fair cost for out1Scaled"
        );
    }
}
```

The fuzzer will find inputs where `consumed == remainingIn` but `out1Scaled` is strictly less than what `remainingIn` should buy at the bin curve price, confirming the overpayment.

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L721-736)
```text
        targetPos = computeAnalyticalTargetPosForSellToken0(
          currBinPos,
          minFinalBinPos,
          state.amountSpecifiedRemainingScaled,
          binState.token1BalanceScaled,
          lowerPriceX64,
          upperPriceX64,
          currBinSellFeeX64
        );
        out1Scaled = calculateOutputToken1FromBinPosition(binState.token1BalanceScaled, currBinPos, targetPos);

        invertedFinalPriceX64 =
          invertPriceX64(calculatePriceAtBinPosition(lowerPriceX64, upperPriceX64, targetPos, Math.Rounding.Floor));
        avgPriceX64 = calculateArithmeticMean(invertedStartingPriceX64, invertedFinalPriceX64);
        in0WithoutFeeScaled = calculateRequiredToken(out1Scaled, avgPriceX64);
        totalIn0Scaled = grossInputWithBinFeeCeil(in0WithoutFeeScaled, onePlusSellFeeX64);
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L738-757)
```text
        if (totalIn0Scaled < state.amountSpecifiedRemainingScaled && targetPos > minFinalBinPos) {
          if (totalIn0Scaled == 0) totalIn0Scaled = 1;

          uint256 delta = currBinPos - targetPos;
          // remaining > totalIn0Scaled ⇒ scaledDelta > delta, may exceed MAX_POS_BIN → keep uint256
          uint256 scaledDelta = Math.ceilDiv(delta * state.amountSpecifiedRemainingScaled, totalIn0Scaled);
          if (scaledDelta == 0) scaledDelta = 1;
          targetPos = currBinPos > scaledDelta ? currBinPos - scaledDelta : 0;
          if (targetPos < minFinalBinPos) {
            targetPos = minFinalBinPos;
          }

          out1Scaled = calculateOutputToken1FromBinPosition(binState.token1BalanceScaled, currBinPos, targetPos);

          invertedFinalPriceX64 =
            invertPriceX64(calculatePriceAtBinPosition(lowerPriceX64, upperPriceX64, targetPos, Math.Rounding.Floor));
          avgPriceX64 = calculateArithmeticMean(invertedStartingPriceX64, invertedFinalPriceX64);
          in0WithoutFeeScaled = calculateRequiredToken(out1Scaled, avgPriceX64);
          totalIn0Scaled = grossInputWithBinFeeCeil(in0WithoutFeeScaled, onePlusSellFeeX64);
        }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L758-760)
```text
        if (totalIn0Scaled < state.amountSpecifiedRemainingScaled && targetPos > minFinalBinPos) {
          totalIn0Scaled = state.amountSpecifiedRemainingScaled;
        }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L762-773)
```text
        if (totalIn0Scaled > state.amountSpecifiedRemainingScaled) {
          uint256 delta = currBinPos - targetPos;
          // remaining < totalIn0Scaled ⇒ ratio < 1 ⇒ scaledDelta ≤ delta ≤ currBinPos ≤ MAX_POS_BIN
          uint256 scaledDelta =
            Math.mulDiv(delta, state.amountSpecifiedRemainingScaled, totalIn0Scaled, Math.Rounding.Ceil);
          if (scaledDelta == 0) scaledDelta = 1;
          targetPos = currBinPos > scaledDelta ? currBinPos - scaledDelta : 0;

          // Rescale out1Scaled proportionally; remaining < totalIn0Scaled ⇒ result ≤ out1Scaled ≤ MAX_POS_BIN
          out1Scaled = (out1Scaled * state.amountSpecifiedRemainingScaled) / totalIn0Scaled;
          totalIn0Scaled = state.amountSpecifiedRemainingScaled;
        }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L783-784)
```text
      state.amountSpecifiedRemainingScaled -= totalIn0Scaled;
      state.amountCalculatedScaled += out1Scaled;
```
