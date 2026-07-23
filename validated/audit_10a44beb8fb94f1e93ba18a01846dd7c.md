Audit Report

## Title
`swap()` Silently Succeeds with Zero Output When No Liquidity Is Available — (`metric-core/contracts/MetricOmmPool.sol`)

## Summary

`MetricOmmPool.swap()` does not revert when the swap produces zero output due to exhausted liquidity or a price limit already being satisfied. All four internal swap helpers contain early-exit or silent-cap paths that return `(0, 0, 0)` / `(0, 0, 0, 0)`, which propagates back through `_executeSwap` to `swap()`, which then invokes the callback with `(0, 0)`, emits a `Swap` event with zero amounts, and returns `(0, 0)` — all without reverting.

## Finding Description

**Confirmed early-exit paths (all verified in production code):**

1. `_swapToken0ForToken1SpecifiedInput` (L1148–1149): `if (params.priceLimitX64 >= initialPriceX64) { return (0, 0, 0); }` — price limit already satisfied. [1](#0-0) 

2. `_swapToken1ForToken0SpecifiedInput` (L970–972): `if (params.priceLimitX64 <= initialPriceX64) { return (0, 0, 0); }` — same for the opposite direction. [2](#0-1) 

3. `_swapToken0ForToken1SpecifiedOutput` (L1049–1052): `amountOutScaled` is silently capped to `binTotals.scaledToken1` before the loop. When `scaledToken1 == 0`, `amountOutScaled` becomes 0, `state.amountSpecifiedRemainingScaled` is initialized to 0, the while loop never executes, and `(0, 0, 0, 0)` is returned. [3](#0-2) 

4. `_swapToken1ForToken0SpecifiedOutput` (L872–876): Same silent cap for `scaledToken0`. [4](#0-3) 

**Consequence in `swap()`:** When `amount0Delta = 0` and `amount1Delta = 0`:
- Neither `amount1Delta < 0` nor `amount0Delta < 0` is true, so no tokens are transferred to `recipient`.
- `metricOmmSwapCallback(0, 0, callbackData)` is called — the caller pays nothing.
- The `IncorrectDelta` guard (`amount0Delta > 0`) is false, so it is skipped entirely.
- `emit Swap(...)` fires with zero amounts.
- `(0, 0)` is returned to the caller. [5](#0-4) 

**The only guard** is `require(amountSpecified != 0, InvalidAmount())` at entry (L225), which checks the *requested* amount, not the *executed* amount. No post-`_executeSwap` check for zero deltas exists anywhere in the production code (confirmed by grep search). [6](#0-5) 

## Impact Explanation

Any router, aggregator, or user calling `swap()` with a non-zero `amountSpecified` receives a successful transaction and a `(0, 0)` return value when the pool has no liquidity in the requested direction or when the price limit is already satisfied. There is no on-chain signal distinguishing "swap executed" from "swap silently did nothing." Downstream logic in routers that assumes a successful `swap()` call delivered tokens will proceed incorrectly — e.g., continuing a multi-hop route with 0 tokens, crediting a user with 0 output, or failing to detect a failed settlement. This constitutes broken core pool swap functionality under the allowed impact gate.

## Likelihood Explanation

Reachable under normal, unprivileged operating conditions:
- A pool fully drained of token1 (all LPs removed token1) will silently accept any `zeroForOne = true` swap — `binTotals.scaledToken1 == 0` triggers the silent cap.
- A caller passing a `priceLimitX64` already satisfied by the current oracle price triggers the early-exit on every call.
- Both conditions require no malicious setup and are reachable by any unprivileged trader or router.

## Recommendation

After `_executeSwap` returns, add a revert if both deltas are zero:

```solidity
if (amount0Delta == 0 && amount1Delta == 0) revert SwapResultedInZeroOutput();
```

This ensures callers receive a descriptive revert rather than a silent no-op, and prevents downstream routers from proceeding with zero tokens.

## Proof of Concept

1. Deploy a pool with token0/token1; add liquidity only to token0-side bins so `binTotals.scaledToken1 == 0`.
2. Call `swap(recipient, true, 1e18, 0, callbackData, "")` — exact-input, selling token0 for token1.
3. Inside `_swapToken0ForToken1SpecifiedInput`, `totalAvailableToken1Scaled == 0` causes the loop to break immediately (L1160–1161).
4. `_executeSwap` returns `(0, 0, 0)`.
5. `swap()` calls `metricOmmSwapCallback(0, 0, callbackData)` — caller pays nothing.
6. `emit Swap(msg.sender, recipient, true, 0, 0, curBinIdx, curPosInBin, 0)` fires.
7. `swap()` returns `(0, 0)` — transaction succeeds with no state change.
8. Recipient received 0 token1; caller paid 0 token0. [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L225-225)
```text
    require(amountSpecified != 0, InvalidAmount());
```

**File:** metric-core/contracts/MetricOmmPool.sol (L250-278)
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

**File:** metric-core/contracts/MetricOmmPool.sol (L872-876)
```text
        uint256 totalAvailableToken0Scaled = binTotals.scaledToken0;
        if (amountOutScaled > totalAvailableToken0Scaled) {
          amountOutScaled = totalAvailableToken0Scaled;
        }
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L970-972)
```text
      if (params.priceLimitX64 <= initialPriceX64) {
        return (0, 0, 0);
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1049-1052)
```text
        uint256 totalAvailableToken1Scaled = binTotals.scaledToken1;
        if (amountOutScaled > totalAvailableToken1Scaled) {
          amountOutScaled = totalAvailableToken1Scaled;
        }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1148-1150)
```text
      if (params.priceLimitX64 >= initialPriceX64) {
        return (0, 0, 0);
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1154-1162)
```text
      while (state.amountSpecifiedRemainingScaled > 0) {
        bool nonEmptyBin = true;
        if (binState.token1BalanceScaled == 0 || curPosInBinCache == 0) {
          if (params.priceLimitX64 != 0 && params.priceLimitX64 >= lowerPriceX64) {
            break;
          }
          if (totalAvailableToken1Scaled == 0) {
            break;
          }
```
