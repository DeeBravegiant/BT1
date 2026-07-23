### Title
Missing `priceLimitX64 != 0` Guard in Initial Price-Limit Check Silently Blocks Valid `!zeroForOne` Swaps — (File: `metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

In `_swapToken1ForToken0SpecifiedInput` and `_swapToken1ForToken0SpecifiedOutput`, the initial price-limit early-exit check is missing the `priceLimitX64 != 0` guard that is explicitly present in the inner swap loop. When a caller passes `priceLimitX64 == 0` (the conventional "no upper price limit" sentinel), the condition `0 <= initialPriceX64` is always true because prices are always positive, causing both functions to immediately return zero amounts. The swap transaction succeeds without reverting but transfers nothing to the recipient.

---

### Finding Description

The inner loop of `_swapToken1ForToken0SpecifiedInput` correctly treats `priceLimitX64 == 0` as "no limit":

```solidity
if (params.priceLimitX64 != 0 && params.priceLimitX64 <= upperPriceX64) {
    break;
}
``` [1](#0-0) 

But the initial guard at the top of both functions lacks this `!= 0` protection:

```solidity
// _swapToken1ForToken0SpecifiedInput
if (params.priceLimitX64 <= initialPriceX64) {
    return (0, 0, 0);
}
``` [2](#0-1) 

```solidity
// _swapToken1ForToken0SpecifiedOutput
if (params.priceLimitX64 <= initialPriceX64) {
    return (0, 0, 0, 0);
}
``` [3](#0-2) 

`initialPriceX64` is derived from `SwapMath.calculatePriceAtBinPosition` applied to bin bounds that are themselves derived from `midPriceX64`, which is always positive (the oracle enforces `bid > 0` and `bid < ask`). Therefore `0 <= initialPriceX64` is unconditionally true, and every `!zeroForOne` swap with `priceLimitX64 == 0` exits immediately with zero amounts before any bin logic executes. [4](#0-3) 

The `swap` entry point imposes no validation that `priceLimitX64 != 0`: [5](#0-4) 

After the early return, `amount0Delta = 0` and `amount1Delta = 0`. The callback is invoked with `(0, 0)`, the `IncorrectDelta` guard (`if (amount1Delta > 0 && ...)`) evaluates false, and the transaction completes successfully — silently delivering zero tokens to the recipient. [6](#0-5) 

---

### Impact Explanation

Any `!zeroForOne` swap (token1 → token0) submitted with `priceLimitX64 == 0` silently returns `(0, 0)`. The recipient receives zero token0. No tokens are debited from the caller either (callback pays 0). The pool state is unchanged. Routers or integrators following the Uniswap V3 convention of passing `0` for "no price limit" will find the entire `!zeroForOne` swap path completely unusable. This constitutes broken core pool swap functionality.

---

### Likelihood Explanation

Medium. The `swap` function accepts `priceLimitX64` as a raw `uint128` with no non-zero requirement. The inner loop's explicit `!= 0` guard is strong evidence the protocol intended `0` to mean "no limit." Any router built on this convention — including the periphery `MetricOmmSimpleRouter` — will silently fail on all `!zeroForOne` swaps with no price limit specified.

---

### Recommendation

Add the `priceLimitX64 != 0` guard to the initial check in both functions, matching the inner loop:

```solidity
// _swapToken1ForToken0SpecifiedInput
if (params.priceLimitX64 != 0 && params.priceLimitX64 <= initialPriceX64) {
    return (0, 0, 0);
}

// _swapToken1ForToken0SpecifiedOutput
if (params.priceLimitX64 != 0 && params.priceLimitX64 <= initialPriceX64) {
    return (0, 0, 0, 0);
}
```

---

### Proof of Concept

1. Deploy a pool with token0/token1 and add liquidity across bins.
2. Call `pool.swap(recipient, false, 1e18, 0, callbackData, "")` — `zeroForOne = false`, `priceLimitX64 = 0`.
3. `_executeSwap` routes to `_swapToken1ForToken0SpecifiedInput` with `params.priceLimitX64 = 0`.
4. `initialPriceX64` is computed from the current bin position — always `> 0`.
5. `0 <= initialPriceX64` → `true` → function returns `(0, 0, 0)` immediately.
6. Back in `_executeSwap`: `amount0DeltaScaled = 0`, `amount1DeltaScaled = 0`.
7. `deltasScaledToExternal(0, 0)` → `amount0Delta = 0`, `amount1Delta = 0`.
8. In `swap`: `if (amount0Delta < 0)` → false, no token0 sent to recipient.
9. Callback invoked with `(0, 0)` — caller pays nothing.
10. `if (amount1Delta > 0 && ...)` → false, no `IncorrectDelta` revert.
11. `swap` returns `(0, 0)`. Recipient receives 0 token0. Swap silently fails.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-225)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());
```

**File:** metric-core/contracts/MetricOmmPool.sol (L271-277)
```text
      uint256 balance1Before = balance1();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount1Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount1Delta > 0 && balance1Before + uint256(amount1Delta) > balance1()) {
        revert IncorrectDelta();
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L806-809)
```text
    try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (uint128 bid, uint128 ask) {
      if (bid >= ask) revert BidGreaterThanAsk();
      if (bid == 0) revert BidIsZero();
      return (bid, ask);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L888-890)
```text
      if (params.priceLimitX64 <= initialPriceX64) {
        return (0, 0, 0, 0);
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L970-972)
```text
      if (params.priceLimitX64 <= initialPriceX64) {
        return (0, 0, 0);
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L979-981)
```text
          if (params.priceLimitX64 != 0 && params.priceLimitX64 <= upperPriceX64) {
            break;
          }
```
