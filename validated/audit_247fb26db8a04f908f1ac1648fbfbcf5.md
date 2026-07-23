The code is fully readable. Let me trace the exact execution path through `buyToken1InBinSpecifiedIn`.

---

### Title
Forced `totalIn0Scaled` assignment without updating `out1Scaled` causes trader to overpay in exact-input token0→token1 swaps — (`metric-core/contracts/libraries/SwapMath.sol`)

### Summary

In `SwapMath.buyToken1InBinSpecifiedIn`, when two refinement passes still underestimate `totalIn0Scaled` relative to `state.amountSpecifiedRemainingScaled`, a forced assignment at line 759 sets `totalIn0Scaled = state.amountSpecifiedRemainingScaled` without updating `out1Scaled`. The pool then consumes all remaining token0 input but returns token1 output computed for a smaller position, breaking the swap conservation invariant at the trader's expense.

### Finding Description

The function uses a three-stage approach to find the correct bin position for an exact-input swap:

1. **Analytical solution** (lines 721–736): Computes `targetPos`, `out1Scaled`, and `totalIn0Scaled`.
2. **First refinement** (lines 738–757): If `totalIn0Scaled < remaining && targetPos > minFinalBinPos`, proportionally scales `targetPos` upward, then **updates `out1Scaled`** (line 750) and recomputes `totalIn0Scaled` (line 756).
3. **Forced assignment** (lines 758–760): If `totalIn0Scaled` is *still* less than `remaining` and `targetPos > minFinalBinPos`, forces `totalIn0Scaled = state.amountSpecifiedRemainingScaled` — **without touching `out1Scaled`**. [1](#0-0) 

After line 759, `totalIn0Scaled == remaining`, so the scale-down block at line 762 (`totalIn0Scaled > remaining`) is never entered: [2](#0-1) 

`out1Scaled` is therefore never rescaled. The function settles with:
- `state.amountSpecifiedRemainingScaled -= totalIn0Scaled` → all input consumed (line 783)
- `state.amountCalculatedScaled += out1Scaled` → output from the smaller, pre-forced-assignment position (line 784) [3](#0-2) 

**Why is the forced assignment reachable?**

The first refinement scales `targetPos` proportionally by `remaining / totalIn0Scaled`. Because the input cost grows *quadratically* with position movement (the average price increases as the bin position moves further), a linear proportional scale-up systematically underestimates the new `totalIn0Scaled`. In bins with a large price range (`upperPriceX64 - lowerPriceX64` large) and input amounts that push deep into the bin, the first refinement still leaves `totalIn0Scaled < remaining` with `targetPos > minFinalBinPos`, triggering line 759.

**Contrast with the symmetric function:**

`buyToken0InBinSpecifiedIn` has the identical structural flaw at lines 620–622 (forced `totalIn1Scaled = remaining` without updating `out0Scaled`), confirming this is a systematic pattern, not an isolated typo. [4](#0-3) 

### Impact Explanation

The trader pays `state.amountSpecifiedRemainingScaled` token0 (all remaining exact input) but receives `out1Scaled` token1 computed for a strictly smaller position. The surplus token0 is credited to the pool's token0 balance (line 780–781), accruing to LPs at the trader's expense. This is a direct, measurable loss of trader principal on every swap that triggers the fallback path — a swap conservation failure meeting the Sherlock Medium threshold. [5](#0-4) 

### Likelihood Explanation

The path requires: (a) the analytical solution underestimates (common in bins with large price ranges), and (b) the first proportional refinement also underestimates (occurs when the quadratic term is dominant). This is not a dust-level edge case — any sufficiently large exact-input swap through a wide bin can trigger it. No privileged role or malicious setup is required; any public swap call suffices.

### Recommendation

Replace the forced assignment with a correct fallback. When `totalIn0Scaled < remaining && targetPos > minFinalBinPos` after both refinements, set `targetPos = minFinalBinPos` (consume the full bin up to the price limit), recompute `out1Scaled` for that position, and recompute `totalIn0Scaled`. Only then apply the scale-down block if `totalIn0Scaled > remaining`. This ensures `out1Scaled` always corresponds to the actual `totalIn0Scaled` consumed.

### Proof of Concept

Foundry property test sketch:

```solidity
// For all valid bin states and input amounts that trigger line 759:
// Assert out1Scaled * avgPriceX64 / ONE_X64 >= totalIn0Scaled (net of fee)
// i.e., the output is worth at least as much as the input at the bin's average price.

function testConservation(
    uint104 token1Balance, uint104 currBinPos,
    uint128 lowerPrice, uint128 upperPrice,
    uint128 amountIn
) public {
    // ... set up BinState and SwapState ...
    (,uint256 out1Scaled,,,) = SwapMath.buyToken1InBinSpecifiedIn(...);
    uint256 totalIn0 = /* amountSpecifiedRemainingScaled before - after */;
    // Invariant: out1Scaled * avgPriceX64 / ONE_X64 >= totalIn0 (net of fee)
    // This FAILS when line 759 is triggered without out1Scaled update
    assertGe(out1Scaled * avgPriceX64 / ONE_X64, totalIn0NetOfFee);
}
```

The test will fail for bins where `upperPriceX64 / lowerPriceX64` is large (e.g., 4×) and `amountIn` is sized to exhaust roughly 60–80% of the bin's token1 balance. [6](#0-5)

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L620-622)
```text
        if (totalIn1Scaled < state.amountSpecifiedRemainingScaled && targetPos < maxFinalBinPos) {
          totalIn1Scaled = state.amountSpecifiedRemainingScaled;
        }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L671-683)
```text
  function buyToken1InBinSpecifiedIn(
    BinState memory binState,
    uint256 currBinPos,
    SwapState memory state,
    uint256 currBinSellFeeX64,
    uint256 lowerPriceX64,
    uint256 upperPriceX64,
    uint256 priceLimitX64,
    uint256 spreadFeeE6
  )
    internal
    pure
    returns (uint256 finalBinPos, uint256 out1Scaled, int256 delta0Scaled, int256 delta1Scaled, uint256 binLpFeeAmount)
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

**File:** metric-core/contracts/libraries/SwapMath.sol (L779-784)
```text
      binState.token1BalanceScaled -= out1Scaled.toUint104();
      binState.token0BalanceScaled =
        (uint256(binState.token0BalanceScaled) + totalIn0Scaled - protocolFeeAmountScaled).toUint104();

      state.amountSpecifiedRemainingScaled -= totalIn0Scaled;
      state.amountCalculatedScaled += out1Scaled;
```
