After carefully tracing the full swap path through `MetricOmmPool.sol::swap` → `_swapToken0ForToken1SpecifiedOutput` → `SwapMath::buyToken1InBinSpecifiedOut`, I found a real, exploitable bug. The "non-zero fee accumulators" and "decimal asymmetry" framing in the question are red herrings — the bug is a straightforward ternary inversion in the price-limit clamping logic.

---

### Title
Price Limit Not Enforced on Output Amount in `buyToken1InBinSpecifiedOut` — (File: `metric-core/contracts/libraries/SwapMath.sol`)

### Summary

In `SwapMath.buyToken1InBinSpecifiedOut`, when the price limit clamps `finalBinPos` to a higher value (less movement), the `amountOutScaled` is incorrectly kept at the **original larger value** via a `max()` instead of being reduced to the smaller clamped-position amount via `min()`. This is a copy-paste inversion compared to the correct implementation in `buyToken0InBinSpecifiedOut`. The result is that the pool outputs more token1 than the price limit permits, corrupting the bin's token1 balance relative to its recorded position.

### Finding Description

**The bug — line 476 of `SwapMath.sol`:**

```solidity
// BUGGY (takes MAX):
amountOutScaled = amountOutHelper < amountOutScaled ? amountOutScaled : amountOutHelper;
```

<cite repo="Tylerpinwa/2026-07-metric-dev