### Title
`MetricOmmSimpleRouter.metricOmmSwapCallback` Reverts on Zero-Delta Swaps, Breaking Core Swap Flow - (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

### Summary

`MetricOmmPool.swap()` unconditionally invokes the caller's `metricOmmSwapCallback` even when the swap produces zero net token deltas (e.g., price limit already past the current bin price, or pool has no liquidity). `MetricOmmSimpleRouter.metricOmmSwapCallback` guards against this with `if (amount0Delta <= 0 && amount1Delta <= 0) revert InvalidSwapDeltas()`, which fires on `(0, 0)` and causes the entire swap transaction to revert. The `IMetricOmmSwapCallback` interface explicitly documents that both deltas may be zero, so the pool's behavior is correct; the router's guard is the broken invariant.

### Finding Description

**Pool side — callback is always called:**

In `MetricOmmPool.swap()`, after `_executeSwap` returns, the callback is invoked unconditionally:

```solidity
// zeroForOne branch
uint256 balance0Before = balance0();
IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
```

There is no guard such as `if (amount0Delta != 0 || amount1Delta != 0)` before the call. When the swap engine returns early with `(0, 0, 0)` — which happens when `priceLimitX64` is already past the current bin price — `amount0Delta = 0` and `amount1Delta = 0` after `deltasScaledToExternal`, and the callback is still invoked with `(0, 0, callbackData)`.

The two early-return paths that produce `(0, 0, 0)`:

```solidity
// _swapToken0ForToken1SpecifiedInput
if (params.priceLimitX64 >= initialPriceX64) {
    return (0, 0, 0);
}
// _swapToken1ForToken0SpecifiedInput
if (params.priceLimitX64 <= initialPriceX64) {
    return (0, 0, 0);
}
```

**Router side — callback rejects `(0, 0)`:**

```solidity
function metricOmmSwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external override {
    if (amount0Delta <= 0 && amount1Delta <= 0) revert InvalidSwapDeltas();
    ...
}
```

`0 <= 0` is true for both arguments, so `InvalidSwapDeltas` is thrown, reverting the entire `swap()` call.

**Interface contract violated:**

The `IMetricOmmSwapCallback` NatSpec explicitly states:

> Both deltas may be zero if no settlement is required for that step.

The router violates this contract.

### Impact Explanation

Any swap routed through `MetricOmmSimpleRouter` (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) reverts with `InvalidSwapDeltas` whenever the pool's swap engine produces zero net deltas. This includes:

- A price limit that is already past the current marginal price at transaction execution time (common in volatile markets where the price moved between submission and inclusion).
- A pool whose active bin has been fully drained and the price limit prevents crossing into the next bin.

The revert is opaque (`InvalidSwapDeltas` rather than `InsufficientOutput`), and it occurs before the `amountOutMinimum` slippage guard can fire, so callers cannot distinguish a price-limit no-op from a genuine settlement failure. Core swap functionality through the only production router is broken for a reachable, non-adversarial input class.

### Likelihood Explanation

The condition is triggered by normal market operation: any swap submitted with a `priceLimitX64` that the oracle price has already crossed by the time the transaction is mined. This is a routine occurrence on any active pool. No privileged access, malicious setup, or non-standard token is required.

### Recommendation

Guard the callback invocation in the pool, or handle zero deltas in the router:

**Option A — Pool skips callback when no settlement is needed (preferred):**

```solidity
// In MetricOmmPool.swap(), zeroForOne branch:
if (amount0Delta > 0 || amount1Delta != 0) {
    uint256 balance0Before = balance0();
    IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
    if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
    }
}
```

**Option B — Router tolerates zero deltas:**

```solidity
function metricOmmSwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external override {
    if (amount0Delta <= 0 && amount1Delta <= 0) return; // no-op swap, nothing to pay
    ...
}
```

### Proof of Concept

1. Deploy a pool with liquidity in bin 0 (current bin).
2. Call `exactInputSingle` through `MetricOmmSimpleRouter` with `zeroForOne = true` and `priceLimitX64` set to a value **above** the current marginal price (i.e., already past the ask side).
3. Inside `_swapToken0ForToken1SpecifiedInput`, `params.priceLimitX64 >= initialPriceX64` is true → returns `(0, 0, 0)`.
4. `_executeSwap` returns `amount0Delta = 0`, `amount1Delta = 0`.
5. Pool calls `metricOmmSwapCallback(0, 0, callbackData)` on the router.
6. Router fires `if (0 <= 0 && 0 <= 0) revert InvalidSwapDeltas()`.
7. Transaction reverts — swap is unusable for this valid input. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L46-48)
```text
  function metricOmmSwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external override {
    if (amount0Delta <= 0 && amount1Delta <= 0) revert InvalidSwapDeltas();

```

**File:** metric-core/contracts/MetricOmmPool.sol (L250-263)
```text
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1148-1150)
```text
      if (params.priceLimitX64 >= initialPriceX64) {
        return (0, 0, 0);
      }
```

**File:** metric-core/contracts/interfaces/callbacks/IMetricOmmSwapCallback.sol (L8-9)
```text
///      Negative deltas mean the pool sends tokens out (handled by the pool before this callback for output legs).
///      Both deltas may be zero if no settlement is required for that step.
```
