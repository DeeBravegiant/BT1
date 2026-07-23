### Title
Missing `priceLimitX64 == 0` Special-Case in `!zeroForOne` Swap Paths Causes Silent Total Swap Failure — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

When `priceLimitX64 == 0` is passed to `swap()` to signal "no price limit," every `!zeroForOne` swap (buying token0 with token1) silently returns `(0, 0)` and transfers nothing. The initial price-limit guard in both `_swapToken1ForToken0SpecifiedOutput` and `_swapToken1ForToken0SpecifiedInput` evaluates `0 <= initialPriceX64`, which is always `true` for any valid uint256 price, causing an immediate early return before any bin is touched.

---

### Finding Description

The `swap()` function accepts `priceLimitX64 = 0` without reverting. The codebase itself treats `0` as "no limit" in at least one place: the loop guard inside `_swapToken1ForToken0SpecifiedInput`:

```solidity
// line 979 — correctly skips the limit check when priceLimitX64 == 0
if (params.priceLimitX64 != 0 && params.priceLimitX64 <= upperPriceX64) {
    break;
}
```

However, the **initial** guard that runs before the loop in the same function does not carry the same `!= 0` special case:

```solidity
// line 970 — missing != 0 guard
if (params.priceLimitX64 <= initialPriceX64) {
    return (0, 0, 0);
}
```

When `priceLimitX64 == 0`, the comparison becomes `0 <= initialPriceX64`, which is always `true` for any valid uint256 price (bid > 0 is enforced by `_getBidAndAskPriceX64`). The function returns `(0, 0, 0)` immediately — the `!= 0` guard inside the loop is dead code that is never reached.

The exact same flaw exists in `_swapToken1ForToken0SpecifiedOutput`:

```solidity
// line 888 — missing != 0 guard
if (params.priceLimitX64 <= initialPriceX64) {
    return (0, 0, 0, 0);
}

// line 895 — also missing != 0 guard (unlike the specifiedInput counterpart)
if (params.priceLimitX64 <= upperPriceX64) {
    break;
}
```

Both the initial guard and the loop guard are missing the `!= 0` special case in the specified-output path.

**Structural parallel to the Blur M-03 bug:**

| Blur Exchange | Metric OMM |
|---|---|
| Old: `expirationTime == 0 \|\| block.timestamp < expirationTime` | Intended: `priceLimitX64 == 0 \|\| priceLimitX64 > initialPriceX64` |
| New (broken): `block.timestamp < expirationTime` | Actual (broken): `priceLimitX64 <= initialPriceX64` |
| Effect: orders with `expirationTime == 0` always fail | Effect: `!zeroForOne` swaps with `priceLimitX64 == 0` always return nothing |

---

### Impact Explanation

Any caller that passes `priceLimitX64 = 0` to mean "no price limit" on a `!zeroForOne` swap receives `(0, 0)` back from `swap()`. The pool emits a `Swap` event with zero deltas, transfers nothing to the recipient, and calls the callback with `(0, 0, callbackData)`. If the callback unconditionally sends tokens to the pool (e.g., a router that pays before checking return values), those tokens are permanently stranded in the pool as unaccounted surplus — a direct loss of user principal. Even without a malformed callback, the core swap functionality is completely broken for this input, which is a valid and documented usage pattern (evidenced by the `!= 0` guard already present in the loop of `_swapToken1ForToken0SpecifiedInput`).

**Severity: Medium** — broken core pool functionality; potential for token loss in router/aggregator integrations that do not validate zero-delta returns.

---

### Likelihood Explanation

`priceLimitX64 = 0` as "no limit" is the standard convention in oracle-anchored and bin-based AMMs (analogous to `sqrtPriceLimitX96 = 0` in Uniswap v3). The presence of the `!= 0` guard in the loop of `_swapToken1ForToken0SpecifiedInput` confirms the protocol intended to support this. Any router, aggregator, or user following this convention on a `!zeroForOne` swap will silently receive nothing.

---

### Recommendation

Add the `!= 0` guard to every price-limit comparison in the `!zeroForOne` swap paths:

```solidity
// _swapToken1ForToken0SpecifiedOutput — initial guard (line 888)
if (params.priceLimitX64 != 0 && params.priceLimitX64 <= initialPriceX64) {
    return (0, 0, 0, 0);
}

// _swapToken1ForToken0SpecifiedOutput — loop guard (line 895)
if (params.priceLimitX64 != 0 && params.priceLimitX64 <= upperPriceX64) {
    break;
}

// _swapToken1ForToken0SpecifiedInput — initial guard (line 970)
if (params.priceLimitX64 != 0 && params.priceLimitX64 <= initialPriceX64) {
    return (0, 0, 0);
}
// (loop guard at line 979 is already correct)
```

---

### Proof of Concept

1. Deploy a pool with token0/token1 and seed it with liquidity across several bins.
2. Call `pool.swap(recipient, false, 1000e18, 0, callbackData, "")` — exact-in, buying token0, `priceLimitX64 = 0`.
3. Observe: `_swapToken1ForToken0SpecifiedInput` hits line 970: `0 <= initialPriceX64` → `true` → returns `(0, 0, 0)`.
4. `_executeSwap` returns `(amount0DeltaScaled=0, amount1DeltaScaled=0, protocolFee=0)`.
5. `swap()` calls `metricOmmSwapCallback(0, 0, callbackData)` and emits `Swap` with zero deltas.
6. Recipient receives no token0; caller pays no token1. Swap silently no-ops.
7. Repeat with `amountSpecified = -1000e18` (exact-out): `_swapToken1ForToken0SpecifiedOutput` hits line 888 with the same result. [1](#0-0) [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L866-900)
```text
  function _swapToken1ForToken0SpecifiedOutput(uint256 amountOutScaled, SwapMath.InternalSwapParams memory params)
    internal
    returns (uint256, uint256, uint256, uint256)
  {
    unchecked {
      {
        uint256 totalAvailableToken0Scaled = binTotals.scaledToken0;
        if (amountOutScaled > totalAvailableToken0Scaled) {
          amountOutScaled = totalAvailableToken0Scaled;
        }
      }
      (
        BinState memory binState,
        SwapMath.SwapState memory state,
        int256 curBinIdxCache,
        uint256 curPosInBinCache,
        int256 curBinDistE6Cache,
        uint256 lowerPriceX64,
        uint256 upperPriceX64,
        uint256 initialPriceX64
      ) = _getInitialStateForSwap(false, true, params, amountOutScaled);

      if (params.priceLimitX64 <= initialPriceX64) {
        return (0, 0, 0, 0);
      }

      while (state.amountSpecifiedRemainingScaled > 0) {
        bool nonEmptyBin = true;
        if (binState.token0BalanceScaled == 0 || curPosInBinCache >= type(uint104).max) {
          if (params.priceLimitX64 <= upperPriceX64) {
            break;
          }
          nonEmptyBin = false;
        }

```

**File:** metric-core/contracts/MetricOmmPool.sol (L954-986)
```text
  function _swapToken1ForToken0SpecifiedInput(uint256 amountInScaled, SwapMath.InternalSwapParams memory params)
    internal
    returns (uint256, uint256, uint256)
  {
    unchecked {
      (
        BinState memory binState,
        SwapMath.SwapState memory state,
        int256 curBinIdxCache,
        uint256 curPosInBinCache,
        int256 curBinDistE6Cache,
        uint256 lowerPriceX64,
        uint256 upperPriceX64,
        uint256 initialPriceX64
      ) = _getInitialStateForSwap(false, false, params, amountInScaled);

      if (params.priceLimitX64 <= initialPriceX64) {
        return (0, 0, 0);
      }

      uint256 totalAvailableToken0Scaled = binTotals.scaledToken0;

      while (state.amountSpecifiedRemainingScaled > 0) {
        bool nonEmptyBin = true;
        if (binState.token0BalanceScaled == 0 || curPosInBinCache >= type(uint104).max) {
          if (params.priceLimitX64 != 0 && params.priceLimitX64 <= upperPriceX64) {
            break;
          }
          if (totalAvailableToken0Scaled == 0) {
            break;
          }
          nonEmptyBin = false;
        }
```
