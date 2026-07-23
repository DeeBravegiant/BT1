Audit Report

## Title
`swap()` Silently Succeeds with Zero Output When No Liquidity Is Available or Price Limit Is Pre-Satisfied — (`metric-core/contracts/MetricOmmPool.sol`)

## Summary

`MetricOmmPool.swap()` does not revert when the executed swap produces zero output. All four internal swap helpers contain early-exit or silent-cap paths that return `(0, 0, 0)` / `(0, 0, 0, 0)` without reverting. `_executeSwap` propagates these zeros back to `swap()`, which then invokes the caller's callback with `(0, 0)`, skips all token transfers, emits a `Swap` event with zero amounts, and returns `(0, 0)` — a successful transaction that did nothing. The sole guard, `require(amountSpecified != 0, InvalidAmount())`, checks the *requested* amount, not the *executed* amount.

## Finding Description

Four confirmed early-exit / silent-cap paths exist in production code:

**Path 1 — `_swapToken0ForToken1SpecifiedInput` (L1148–1150):** If `params.priceLimitX64 >= initialPriceX64`, the function returns `(0, 0, 0)` immediately before the swap loop.

**Path 2 — `_swapToken1ForToken0SpecifiedInput` (L970–972):** Symmetric price-limit early exit returns `(0, 0, 0)`.

**Path 3 — `_swapToken0ForToken1SpecifiedOutput` (L1049–1052 + L1066–1068):** `amountOutScaled` is silently capped to `binTotals.scaledToken1`. When that value is zero, `amountOutScaled = 0` is passed to `_getInitialStateForSwap`, setting `state.amountSpecifiedRemainingScaled = 0`, so the while loop never executes and the function returns `(0, 0, 0, 0)`. A separate price-limit early exit at L1066–1068 also returns `(0, 0, 0, 0)`.

**Path 4 — `_swapToken1ForToken0SpecifiedOutput` (L872–875 + L888–890):** Symmetric zero-cap and price-limit early exits return `(0, 0, 0, 0)`.

In `_executeSwap` (L672–802), when any helper returns `(0, 0, ...)`, `amount0DeltaScaled = 0` and `amount1DeltaScaled = 0`. The `binTotals` update at L735–739 / L743–747 is a no-op (adds and subtracts zero). `_executeSwap` returns `(0, 0, 0)` to `swap()`.

In `swap()` (L217–301):
1. `if (amount1Delta < 0)` — false; no token1 transferred to recipient.
2. `metricOmmSwapCallback(0, 0, callbackData)` — callback invoked; caller pays nothing.
3. `if (amount0Delta > 0 && ...)` — false; `IncorrectDelta` guard skipped.
4. `emit Swap(..., 0, 0, ...)` fires.
5. `return (0, 0)` — transaction succeeds.

The only guard is `require(amountSpecified != 0, InvalidAmount())` at L225, which is satisfied by any non-zero requested amount.

## Impact Explanation

Any router, aggregator, or protocol calling `swap()` with a non-zero `amountSpecified` receives a successful transaction and `(0, 0)` return when the pool has no liquidity in the requested direction or when the price limit is already satisfied. The caller has no on-chain signal distinguishing "swap executed" from "swap silently did nothing." Multi-hop routers that chain swaps based on the output of a prior `swap()` call will proceed with zero tokens, breaking downstream legs. This constitutes broken core pool swap functionality: a valid non-zero swap request produces no state change, no token delivery, and no revert.

## Likelihood Explanation

Both triggering conditions are unprivileged and reachable under normal operating conditions:
- A pool where all token1 has been removed by LPs (`binTotals.scaledToken1 == 0`) silently accepts any `zeroForOne = true` swap.
- A caller passing a `priceLimitX64` already satisfied by the current oracle price triggers the early-exit on every call.
Neither condition requires malicious setup or privileged access.

## Recommendation

After `_executeSwap` returns in `swap()`, add a check that reverts if both deltas are zero:

```solidity
if (amount0Delta == 0 && amount1Delta == 0) revert SwapResultedInZeroOutput();
```

This should be placed after L248 (after `_executeSwap`) and before the callback invocation. Alternatively, each internal helper should revert rather than return `(0, 0, 0)` when no execution occurred.

## Proof of Concept

1. Deploy a pool with token0/token1. Add liquidity only to token0-side bins so that `binTotals.scaledToken1 == 0`.
2. Call `swap(recipient, true, 1e18, 0, callbackData, "")` — exact-input, selling token0 for token1.
3. Inside `_swapToken0ForToken1SpecifiedInput` (L1152), `totalAvailableToken1Scaled == 0`. The while loop at L1154 breaks immediately at L1160–1162.
4. Function returns `(0, 0, 0)` (amountIn consumed = 0, amountOut = 0).
5. `_executeSwap` sets `amount0DeltaScaled = 0`, `amount1DeltaScaled = 0`, returns `(0, 0, 0)`.
6. `swap()` skips token transfer, calls `metricOmmSwapCallback(0, 0, callbackData)` — caller pays nothing.
7. `emit Swap(msg.sender, recipient, true, 0, 0, ...)` fires.
8. `swap()` returns `(0, 0)`. Transaction succeeds. Recipient received 0 token1; caller paid 0 token0.