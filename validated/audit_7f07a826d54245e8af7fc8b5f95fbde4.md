Audit Report

## Title
Unconditional `metricOmmSwapCallback` Invocation on Zero-Delta Swaps Causes DoS via `InvalidSwapDeltas` Revert — (`metric-core/contracts/MetricOmmPool.sol` / `metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

## Summary

`MetricOmmPool.swap` unconditionally invokes `metricOmmSwapCallback` on the caller at lines 258 and 272 regardless of whether the swap produced any non-zero token deltas. [1](#0-0)  `MetricOmmSimpleRouter.metricOmmSwapCallback` unconditionally reverts with `InvalidSwapDeltas` when both deltas are `<= 0`. [2](#0-1)  When a swap legitimately produces `(0, 0)` deltas — because the price limit is already satisfied at the current cursor position — the pool calls the router's callback with `(0, 0)`, the router reverts, and the entire swap transaction reverts with a misleading error.

## Finding Description

**Root cause in `MetricOmmPool.swap`:** After `_executeSwap` returns `(amount0Delta, amount1Delta, protocolFeeAmount)`, the pool unconditionally calls the callback with no guard for the zero-delta case:

```solidity
uint256 balance0Before = balance0();
IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
``` [3](#0-2) 

The same unconditional pattern exists for the `!zeroForOne` branch at line 272. [4](#0-3) 

**Triggering condition in swap math:** The internal swap helpers return `(0, 0, 0)` immediately when the price limit is already satisfied at the initial cursor position. For `_swapToken1ForToken0SpecifiedInput`:

```solidity
if (params.priceLimitX64 <= initialPriceX64) {
    return (0, 0, 0);
}
``` [5](#0-4) 

And for `_swapToken0ForToken1SpecifiedInput`:

```solidity
if (params.priceLimitX64 >= initialPriceX64) {
    return (0, 0, 0);
}
``` [6](#0-5) 

After `deltasScaledToExternal`, both `amount0Delta` and `amount1Delta` are `0`.

**Revert in `MetricOmmSimpleRouter.metricOmmSwapCallback`:**

```solidity
function metricOmmSwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external override {
    if (amount0Delta <= 0 && amount1Delta <= 0) revert InvalidSwapDeltas();
``` [2](#0-1) 

This guard does not distinguish between a caller-supplied invalid swap and a pool-reported zero-output swap. The full call chain is: `exactInputSingle` → `pool.swap` → `_executeSwap` → returns `(0,0,0)` → `metricOmmSwapCallback(0, 0, "")` → `revert InvalidSwapDeltas()`. [7](#0-6) 

## Impact Explanation

Any user routing through `MetricOmmSimpleRouter` (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) whose swap resolves to zero output — due to a price limit already exceeded at the current bin cursor, or a pool with no liquidity in reachable bins — receives an `InvalidSwapDeltas` revert instead of a graceful zero-output result. This breaks the core swap flow for the router, which is the primary user-facing entry point. Users who set `amountOutMinimum = 0` (explicitly accepting zero output) are still blocked. This constitutes broken core pool functionality causing an unusable swap flow, matching the allowed impact gate. [2](#0-1) 

## Likelihood Explanation

The condition is reachable by any unprivileged caller without special setup: (1) a pool whose current bin cursor is at a price already beyond `priceLimitX64` (common during volatile oracle moves); (2) a pool with no liquidity in the current bin and a price limit that prevents bin traversal; (3) any `exactInput` multi-hop where an intermediate pool returns zero output. No privileged role or malicious setup is required. [5](#0-4) 

## Recommendation

Guard the callback invocation in `MetricOmmPool.swap` to skip it when both deltas are zero:

```solidity
if (amount0Delta != 0 || amount1Delta != 0) {
    IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
}
```

Apply this guard to both the `zeroForOne` branch (line 258) and the `!zeroForOne` branch (line 272). [8](#0-7) 

Alternatively, relax the `InvalidSwapDeltas` guard in `MetricOmmSimpleRouter.metricOmmSwapCallback` to treat `(0, 0)` as a no-op rather than an error. [2](#0-1) 

## Proof of Concept

1. Deploy a pool where the current `initialPriceX64` for a `zeroForOne` swap already satisfies `priceLimitX64 >= initialPriceX64`.
2. Call `MetricOmmSimpleRouter.exactInputSingle` with `amountOutMinimum = 0` and the same `priceLimitX64`.
3. Inside `pool.swap`, `_swapToken0ForToken1SpecifiedInput` returns `(0, 0, 0)` immediately at line 1148–1150.
4. `_executeSwap` returns `amount0Delta = 0`, `amount1Delta = 0`.
5. `pool.swap` calls `router.metricOmmSwapCallback(0, 0, "")` unconditionally at line 258.
6. Router reverts: `if (amount0Delta <= 0 && amount1Delta <= 0) revert InvalidSwapDeltas()`.
7. `exactInputSingle` reverts with `InvalidSwapDeltas` despite `amountOutMinimum = 0`. [9](#0-8)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L247-278)
```text
    (int256 amount0Delta, int256 amount1Delta, uint256 protocolFeeAmount) =
      _executeSwap(zeroForOne, amountSpecified, params);

    if (zeroForOne) {
      if (amount1Delta < 0) {
        // casting to uint256 is safe because amount1Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken1(recipient, uint256(-amount1Delta));
      }

      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
    } else {
      if (amount0Delta < 0) {
        // casting to uint256 is safe because amount0Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken0(recipient, uint256(-amount0Delta));
      }

      uint256 balance1Before = balance1();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount1Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount1Delta > 0 && balance1Before + uint256(amount1Delta) > balance1()) {
        revert IncorrectDelta();
      }
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L970-972)
```text
      if (params.priceLimitX64 <= initialPriceX64) {
        return (0, 0, 0);
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1148-1150)
```text
      if (params.priceLimitX64 >= initialPriceX64) {
        return (0, 0, 0);
      }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L46-47)
```text
  function metricOmmSwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external override {
    if (amount0Delta <= 0 && amount1Delta <= 0) revert InvalidSwapDeltas();
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
