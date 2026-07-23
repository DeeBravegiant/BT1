The vulnerability is real. Let me trace the complete path precisely.

**`_swapToken1ForToken0SpecifiedOutput` — silent cap before the loop:** [1](#0-0) 

The requested `amountOutScaled` is silently capped to `binTotals.scaledToken0` before the swap loop begins. The original `amountSpecified` is discarded — the function now tracks a reduced target.

**Return value after the loop:** [2](#0-1) 

The actual output returned is `amountOutScaled - state.amountSpecifiedRemainingScaled`, where `amountOutScaled` is already the capped value. No revert, no signal.

**`_executeSwap` propagates the partial fill:** [3](#0-2) 

`amount0DeltaScaled` is set to `-int256(amountOutScaled)` — the partial fill amount, not the requested amount.

**`IncorrectDelta` check only verifies input payment:** [4](#0-3) 

The guard checks only that the caller paid the correct token1 input for the *actual* (capped) output. It never checks whether `amount0Delta` equals the originally requested amount. No revert fires.

The same pattern exists in `_swapToken0ForToken1SpecifiedOutput`: [5](#0-4) 

The per-bin cap in `buyToken0InBinSpecifiedOut` at line 380 is a secondary contributor — it caps within a single bin — but the primary, pool-level silent cap happens before the loop even starts. [6](#0-5) 

---

### Title
Exact-Output Swap Silently Delivers Partial Fill Without Reverting — (`metric-core/contracts/MetricOmmPool.sol`)

### Summary
Both `_swapToken1ForToken0SpecifiedOutput` and `_swapToken0ForToken1SpecifiedOutput` silently cap the requested output to available pool liquidity before the swap loop. When pool liquidity is insufficient, the swap completes with a partial fill, returns reduced deltas to the caller's callback, and never reverts. The `IncorrectDelta` guard only verifies that the input token was paid for the *actual* output — it does not enforce that the output equals the originally specified amount.

### Finding Description
When a caller submits an exact-output swap (`amountSpecified < 0`) requesting more of the output token than the pool holds in `binTotals`, the pool:

1. Caps `amountOutScaled = totalAvailableTokenXScaled` (lines 873–875 / 1050–1052).
2. Runs the bin loop against the capped target.
3. Returns `(amountIn, actualOut, ...)` where `actualOut < requestedOut`.
4. Calls the caller's `metricOmmSwapCallback` with `amount0Delta` (or `amount1Delta`) reflecting the partial fill.
5. Checks only that the caller paid the correct input for the partial fill — never that the output matches the original request.

No revert occurs at any point.

### Impact Explanation
The exact-output invariant — "fill completely or revert" — is broken for both swap directions. Callers that depend on receiving exactly the specified amount (routers completing multi-leg trades, flash-loan repayment paths, arbitrage bots with minimum-receive requirements) will silently receive less than requested. Because the callback is invoked with the partial-fill deltas, any downstream logic that assumes the full amount was received will operate on incorrect state. This constitutes broken core pool functionality and can cause direct loss of funds in downstream operations that depend on the exact output amount.

### Likelihood Explanation
Any exact-output swap where the requested amount exceeds current pool liquidity triggers this path. This is a normal market condition (thin liquidity, large order), not an edge case. No special permissions or setup are required — any caller of the public `swap()` function can reach it.

### Recommendation
After the swap loop, assert that the full requested amount was filled, or revert:

```solidity
// In _swapToken1ForToken0SpecifiedOutput / _swapToken0ForToken1SpecifiedOutput
uint256 actualOut = amountOutScaled - state.amountSpecifiedRemainingScaled;
require(actualOut == originalAmountOutScaled, InsufficientLiquidity());
```

Alternatively, remove the pre-loop cap entirely and let the loop exhaust available bins, then revert if `state.amountSpecifiedRemainingScaled > 0` after the loop exits.

### Proof of Concept
1. Deploy a pool with 500 token0 total across all bins.
2. Call `swap(recipient, false, -1000, priceLimitX64, ...)` (exact-output: request 1000 token0, pay token1).
3. Observe: `_swapToken1ForToken0SpecifiedOutput` caps `amountOutScaled` to 500 at line 874.
4. Swap loop fills 500 token0; `state.amountSpecifiedRemainingScaled` ends at 0 (all 500 consumed).
5. `swap()` returns `amount0Delta = -500`, `amount1Delta = +proportional_input`.
6. No revert. Recipient receives 500 token0, not 1000.
7. Assert: `amount0Delta != -1000` — partial fill confirmed with no revert.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L264-277)
```text
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L717-726)
```text
          // forge-lint: disable-next-line(unsafe-typecast)
          uint256 amountOutScaled = TOKEN_0_SCALE_MULTIPLIER * uint256(-amountSpecified);
          uint256 amountInScaled;
          (amountInScaled, amountOutScaled, protocolFeeScaled, feeExclusiveInputScaled) =
            _swapToken1ForToken0SpecifiedOutput(amountOutScaled, params);
          // forge-lint: disable-next-line(unsafe-typecast)
          amount0DeltaScaled = -int256(amountOutScaled);
          // forge-lint: disable-next-line(unsafe-typecast)
          amount1DeltaScaled = int256(amountInScaled);
        }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L871-876)
```text
      {
        uint256 totalAvailableToken0Scaled = binTotals.scaledToken0;
        if (amountOutScaled > totalAvailableToken0Scaled) {
          amountOutScaled = totalAvailableToken0Scaled;
        }
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L943-948)
```text
      return (
        state.amountCalculatedScaled,
        amountOutScaled - state.amountSpecifiedRemainingScaled,
        state.protocolFeeAmountScaled,
        state.feeExclusiveInputScaled
      );
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1048-1053)
```text
      {
        uint256 totalAvailableToken1Scaled = binTotals.scaledToken1;
        if (amountOutScaled > totalAvailableToken1Scaled) {
          amountOutScaled = totalAvailableToken1Scaled;
        }
      }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L378-381)
```text
      } else {
        finalBinPos = MAX_POS_BIN;
        amountOutScaled = binState.token0BalanceScaled;
      }
```
