Audit Report

## Title
Silent Partial Fill in Exact-Output Swaps When Pool Has Insufficient Liquidity — (File: metric-core/contracts/MetricOmmPool.sol)

## Summary
`_swapToken1ForToken0SpecifiedOutput` (and its symmetric counterpart `_swapToken0ForToken1SpecifiedOutput`) unconditionally clamps the requested exact-output amount to the pool's available balance at L872–875 without reverting. Any caller invoking `pool.swap()` directly with a negative `amountSpecified` will silently receive a partial fill, breaking the exact-output invariant at the pool level. The `MetricOmmSimpleRouter` adds a post-swap revert guard, but this protection is absent from the pool itself, leaving every direct integrator exposed.

## Finding Description
At the entry of `_swapToken1ForToken0SpecifiedOutput`, before the bin-traversal loop, the requested output is unconditionally reduced to whatever the pool currently holds:

```solidity
// metric-core/contracts/MetricOmmPool.sol L872-875
uint256 totalAvailableToken0Scaled = binTotals.scaledToken0;
if (amountOutScaled > totalAvailableToken0Scaled) {
    amountOutScaled = totalAvailableToken0Scaled;   // silent clamp, no revert
}
```

After the clamp, the swap loop runs normally, the callback is invoked, and the pool transfers only the clamped (reduced) amount of token0 to the recipient. The outer `swap` function's `IncorrectDelta` guard at L275–277 only checks that the caller paid enough input tokens for the *reduced* output — not for the originally requested amount:

```solidity
// metric-core/contracts/MetricOmmPool.sol L271-277
uint256 balance1Before = balance1();
IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
if (amount1Delta > 0 && balance1Before + uint256(amount1Delta) > balance1()) {
    revert IncorrectDelta();
}
```

`IncorrectDelta` guards against underpayment for the reduced output; it does not guard against the pool under-delivering relative to the original request. The `MetricOmmSimpleRouter.exactOutputSingle` does add a post-swap check at L138–139 that reverts if `amountOut != expectedAmountOut`, but this protection lives entirely in the periphery and is absent from the pool itself.

## Impact Explanation
Any smart contract integrating directly with `MetricOmmPool.swap()` using a negative `amountSpecified` (exact-output mode) will silently receive fewer tokens than requested. If the caller needed exactly N tokens to repay a flash loan, post collateral, or settle a position, receiving fewer tokens causes those downstream operations to fail or behave incorrectly — after the caller has already spent input tokens. The transaction succeeds on-chain with no error signal, so the caller has no automatic protection unless it explicitly compares return values against the requested amount. This constitutes broken core pool functionality causing potential loss of funds for direct integrators.

## Likelihood Explanation
No privileged access is required. Any unprivileged caller can trigger this path by calling `pool.swap()` directly with a negative `amountSpecified` when pool liquidity is insufficient to fill the full request. Low-liquidity conditions are normal in oracle-anchored bin pools where liquidity is concentrated; a large exact-output request can easily exceed a single bin's balance. The condition is repeatable and requires no special setup.

## Recommendation
Add an explicit revert when the pool cannot honour the full exact-output request:

```solidity
uint256 totalAvailableToken0Scaled = binTotals.scaledToken0;
if (amountOutScaled > totalAvailableToken0Scaled) {
    revert InsufficientLiquidity(amountOutScaled, totalAvailableToken0Scaled);
}
```

Apply the same fix symmetrically to `_swapToken0ForToken1SpecifiedOutput`. This aligns the pool's behaviour with the exact-output contract: either deliver the requested amount or revert, never silently deliver less.

## Proof of Concept
1. Pool holds `binTotals.scaledToken0 = 500e18`.
2. Direct integrator calls:
   ```solidity
   pool.swap(recipient, false, -1000e18, 0, "", "");
   ```
   requesting exactly 1000e18 token0 out.
3. Inside `_swapToken1ForToken0SpecifiedOutput` at L873–874, `amountOutScaled` is silently clamped to `500e18`.
4. The swap loop runs, delivering 500e18 token0 to `recipient`.
5. The callback is invoked; the caller pays token1 for 500e18 token0 worth of output.
6. `IncorrectDelta` does not fire (caller paid correctly for the reduced amount).
7. `swap()` returns `(amount0Delta, amount1Delta)` reflecting only 500e18 token0 — the transaction succeeds.
8. The caller expected 1000e18 token0 and received 500e18, with no on-chain error. Any downstream operation requiring the full 1000e18 token0 now fails or is underfunded.