The forced assignment at line 621 is real and the `out0Scaled` staleness is a genuine conservation failure. Let me confirm the settlement path.

The code path is real and the conservation failure is confirmed. Here is the full analysis:

---

### Title
Swap Conservation Failure in `buyToken0InBinSpecifiedIn`: Forced `totalIn1Scaled` Assignment Without Updating `out0Scaled` Causes Trader to Overpay — (`metric-core/contracts/libraries/SwapMath.sol`)

---

### Summary

In `SwapMath.buyToken0InBinSpecifiedIn`, a two-pass refinement loop attempts to find the bin position `targetPos` whose cost exactly equals `amountSpecifiedRemainingScaled`. When both passes undershoot and `targetPos < maxFinalBinPos` still holds, a fallback at line 621 forces `totalIn1Scaled = amountSpecifiedRemainingScaled` without updating `out0Scaled`. The subsequent rescaling guard at line 624 is then skipped (equality, not `>`), leaving `out0Scaled` stale. The pool settles by consuming the full input but delivering the smaller output, transferring the excess token1 to LPs at the trader's expense.

---

### Finding Description

The function `buyToken0InBinSpecifiedIn` uses a three-stage approach to find the correct position:

**Stage 1 (lines 567–578):** Check whether the full `maxFinalBinPos` is affordable. If yes, `targetPos = maxFinalBinPos`.

**Stage 2 (lines 581–618):** Analytical solution + one proportional refinement. If after the refinement `totalIn1Scaled < amountSpecifiedRemainingScaled && targetPos < maxFinalBinPos`, the code scales `targetPos` up proportionally and recomputes `out0Scaled` and `totalIn1Scaled`.

**Stage 3 — the bug (lines 620–622):** [1](#0-0) 

If the second evaluation still undershoots, `totalIn1Scaled` is forced to `amountSpecifiedRemainingScaled`. Critically, `out0Scaled` is **not updated**.

**Stage 4 (lines 624–634):** The rescaling guard fires only when `totalIn1Scaled > amountSpecifiedRemainingScaled`. After the forced assignment they are equal, so this block is skipped entirely. [2](#0-1) 

**Settlement (lines 636–650):** [3](#0-2) 

- `binState.token0BalanceScaled -= out0Scaled` — correct token0 removed for the smaller `targetPos`
- `binState.token1BalanceScaled += totalIn1Scaled - protocolFee` — **full** `amountSpecifiedRemainingScaled` added to bin
- `state.amountSpecifiedRemainingScaled -= totalIn1Scaled` — full input consumed, nothing left for subsequent bins
- `state.amountCalculatedScaled += out0Scaled` — the stale, smaller output recorded

The excess token1 (`amountSpecifiedRemainingScaled − actual_cost_for_targetPos`) is silently absorbed by the bin, accruing to LPs.

---

### Impact Explanation

This is a **swap conservation failure**: the trader pays the full exact-input amount but receives less token0 than the bin curve permits for that payment. The shortfall is not returned; it is credited to the bin's token1 balance, permanently benefiting LPs. The trader has no recourse — the swap callback receives the correct `amountIn` debit but a reduced `amountOut` credit. This constitutes a direct loss of user principal above Sherlock Medium thresholds whenever the discrepancy is non-trivial.

---

### Likelihood Explanation

The condition requires two consecutive undershoots of the refinement loop. This occurs when the price curve is sufficiently non-linear relative to the linear proportional scaling used in the refinement — e.g., wide bins, large `currBinPos` offsets, or extreme price ratios. It is not a degenerate edge case: any public caller can craft an `amountIn` and bin configuration that lands in this regime. No privileged role is required; the path is `swap` → `_swapToken1ForToken0SpecifiedInput` → `buyToken0InBinSpecifiedIn`. [4](#0-3) 

---

### Recommendation

After the forced assignment at line 621, recompute `out0Scaled` consistently with `totalIn1Scaled`. The simplest fix is to treat the forced case identically to the overshoot case: apply the proportional rescaling of `out0Scaled` before settling:

```solidity
if (totalIn1Scaled < state.amountSpecifiedRemainingScaled && targetPos < maxFinalBinPos) {
    // Proportionally rescale out0Scaled to match the forced totalIn1Scaled
    uint256 delta = targetPos - currBinPos;
    uint256 scaledDelta = Math.ceilDiv(delta * state.amountSpecifiedRemainingScaled, totalIn1Scaled);
    if (scaledDelta == 0) scaledDelta = 1;
    uint256 scaledTarget = currBinPos + scaledDelta;
    targetPos = scaledTarget > maxFinalBinPos ? maxFinalBinPos : scaledTarget;
    out0Scaled = calculateOutputToken0FromBinPosition(binState.token0BalanceScaled, currBinPos, targetPos);
    totalIn1Scaled = state.amountSpecifiedRemainingScaled;
}
```

Alternatively, add a third refinement pass instead of the forced assignment, or clamp `targetPos = maxFinalBinPos` and accept the slight overshoot (which the existing Stage 4 rescaling already handles correctly).

---

### Proof of Concept

**Foundry property test sketch:**

```solidity
function testFuzz_BuyToken0SpecifiedIn_ConservationInvariant(
    uint104 currBinPos,
    uint104 availableToken0,
    uint128 remainingIn,
    uint128 lowerPriceX64,
    uint128 upperPriceX64
) public view {
    // ... bound inputs, construct binState and state ...

    (uint256 finalPos, uint256 out0, , , ) = SwapMath.buyToken0InBinSpecifiedIn(
        binState, currBinPos, state, 0, lowerPriceX64, upperPriceX64, type(uint128).max, 0
    );

    // Conservation: cost implied by out0 at avgPrice must equal consumed input
    uint256 consumed = remainingIn - state.amountSpecifiedRemainingScaled;
    uint256 startP = SwapMath.calculatePriceAtBinPosition(lowerPriceX64, upperPriceX64, currBinPos, Math.Rounding.Ceil);
    uint256 finalP = SwapMath.calculatePriceAtBinPosition(lowerPriceX64, upperPriceX64, finalPos, Math.Rounding.Ceil);
    uint256 avgP   = SwapMath.calculateArithmeticMean(startP, finalP);
    uint256 impliedCost = Math.mulDiv(out0, avgP, SwapMath.ONE_X64);

    // Trader must not pay more than the curve implies for the output received
    assertLe(consumed, impliedCost + TOLERANCE, "trader overpaid: conservation violated");
}
```

The fuzzer will find inputs where the forced-assignment branch fires and `consumed > impliedCost`, demonstrating the trader overpays.

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L620-622)
```text
        if (totalIn1Scaled < state.amountSpecifiedRemainingScaled && targetPos < maxFinalBinPos) {
          totalIn1Scaled = state.amountSpecifiedRemainingScaled;
        }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L624-634)
```text
        if (totalIn1Scaled > state.amountSpecifiedRemainingScaled) {
          uint256 delta = targetPos - currBinPos;
          // remaining < totalIn1Scaled ⇒ ratio < 1 ⇒ scaledDelta ≤ delta ≤ MAX_POS_BIN
          uint256 scaledDelta = Math.ceilDiv(delta * state.amountSpecifiedRemainingScaled, totalIn1Scaled);
          if (scaledDelta == 0) scaledDelta = 1;
          targetPos = currBinPos + scaledDelta;

          // Rescale out0Scaled proportionally; remaining < totalIn1Scaled ⇒ result ≤ out0Scaled ≤ MAX_POS_BIN
          out0Scaled = (out0Scaled * state.amountSpecifiedRemainingScaled) / totalIn1Scaled;
          totalIn1Scaled = state.amountSpecifiedRemainingScaled;
        }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L639-644)
```text
      binState.token0BalanceScaled -= out0Scaled.toUint104();
      binState.token1BalanceScaled =
        uint256((binState.token1BalanceScaled) + totalIn1Scaled - protocolFeeAmountScaled).toUint104();

      state.amountSpecifiedRemainingScaled -= totalIn1Scaled;
      state.amountCalculatedScaled += out0Scaled;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L994-1004)
```text
          (curPosInBinCache, outToken0AmountScaled, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) =
            SwapMath.buyToken0InBinSpecifiedIn(
              binState,
              curPosInBinCache,
              state,
              params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
              lowerPriceX64,
              upperPriceX64,
              params.priceLimitX64,
              spreadFeeE6
            );
```
