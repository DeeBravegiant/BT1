Audit Report

## Title
Exact-Output Swaps Silently Deliver Less Token Than Requested — (`metric-core/contracts/MetricOmmPool.sol`)

## Summary

Both `_swapToken1ForToken0SpecifiedOutput` and `_swapToken0ForToken1SpecifiedOutput` unconditionally clamp the requested output to the pool's available balance before bin iteration begins. The capped value is returned as the actual output, transferred to the recipient, and passed to the callback — with no revert and no on-chain signal that the fill was partial. The public `swap` interface provides no `minAmountOut` guard.

## Finding Description

In `_swapToken1ForToken0SpecifiedOutput`, before any bin iteration, the requested output is silently capped:

```solidity
uint256 totalAvailableToken0Scaled = binTotals.scaledToken0;
if (amountOutScaled > totalAvailableToken0Scaled) {
    amountOutScaled = totalAvailableToken0Scaled;   // silent cap
}
``` [1](#0-0) 

The same pattern exists symmetrically in `_swapToken0ForToken1SpecifiedOutput`: [2](#0-1) 

`_executeSwap` propagates the capped value directly as `amount0DeltaScaled` (or `amount1DeltaScaled`): [3](#0-2) 

`swap` then transfers the reduced token amount to the recipient and invokes the callback with the reduced deltas: [4](#0-3) 

The only post-callback guard is `IncorrectDelta`, which checks that the callback paid enough of the *input* token relative to the (already-reduced) `amount1Delta` — it does not verify that the output matches `amountSpecified`. [5](#0-4) 

The `swap` signature accepts only `priceLimitX64` as a slippage guard, which protects against price movement but not against insufficient pool depth: [6](#0-5) 

A grep across all scoped contracts confirms no `minAmountOut`, `InsufficientOutput`, or partial-fill guard exists anywhere.

## Impact Explanation

A router, aggregator, or protocol that calls `swap` with `amountSpecified < 0` to obtain exactly X tokens will: (1) receive fewer than X tokens transferred to `recipient`; (2) have its callback invoked with a smaller-than-expected delta, causing it to pay less input token than anticipated; (3) receive no revert and no on-chain indication of the partial fill. Downstream logic that assumes the full X tokens were received — e.g., flash-loan repayment, minimum-fill enforcement for a user order, or collateral transfer to a downstream protocol — will operate on incorrect state. This constitutes broken core swap functionality and direct loss of user funds in affected call paths, meeting Sherlock medium/high thresholds.

## Likelihood Explanation

Any pool with thin liquidity relative to a requested exact-output amount is vulnerable. No privileged access is required — any unprivileged caller of `swap` with `amountSpecified < 0` is affected. An attacker can also deliberately drain a pool's token balance in a prior transaction or in the same block to force a victim's exact-output swap to partially fill, with no special permissions needed.

## Recommendation

Add a `minAmountOut` parameter to `swap`. After `_executeSwap` returns, revert if the actual output is below the caller's minimum:

```solidity
// For !zeroForOne exact-output (token0 out):
if (amountSpecified < 0 && uint256(-amount0Delta) < minAmountOut) revert InsufficientOutput();
// For zeroForOne exact-output (token1 out):
if (amountSpecified < 0 && uint256(-amount1Delta) < minAmountOut) revert InsufficientOutput();
```

Alternatively, revert inside `_swapToken*ForToken*SpecifiedOutput` when `amountOutScaled > totalAvailableToken*Scaled` instead of silently capping.

## Proof of Concept

```solidity
// Pool holds only 0.5e18 token0 (scaledToken0 = 0.5e18 * TOKEN_0_SCALE_MULTIPLIER)
pool.swap(
    recipient,
    false,      // zeroForOne = false → buy token0 with token1
    -1e18,      // exact output: wants 1e18 token0
    0,          // no price limit
    "",         // no callbackData
    ""
);
// Result:
//   _swapToken1ForToken0SpecifiedOutput caps amountOutScaled to 0.5e18 * scale
//   recipient receives only 0.5e18 token0
//   callback invoked with amount0Delta = -0.5e18 (not -1e18)
//   no revert; caller's downstream logic expecting 1e18 token0 operates on wrong state
```

Root cause: unconditional silent cap at lines 872–875 (and 1049–1052), combined with the absence of any `minAmountOut` guard in the public `swap` interface.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

**File:** metric-core/contracts/MetricOmmPool.sol (L265-272)
```text
      if (amount0Delta < 0) {
        // casting to uint256 is safe because amount0Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken0(recipient, uint256(-amount0Delta));
      }

      uint256 balance1Before = balance1();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L275-277)
```text
      if (amount1Delta > 0 && balance1Before + uint256(amount1Delta) > balance1()) {
        revert IncorrectDelta();
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L718-725)
```text
          uint256 amountOutScaled = TOKEN_0_SCALE_MULTIPLIER * uint256(-amountSpecified);
          uint256 amountInScaled;
          (amountInScaled, amountOutScaled, protocolFeeScaled, feeExclusiveInputScaled) =
            _swapToken1ForToken0SpecifiedOutput(amountOutScaled, params);
          // forge-lint: disable-next-line(unsafe-typecast)
          amount0DeltaScaled = -int256(amountOutScaled);
          // forge-lint: disable-next-line(unsafe-typecast)
          amount1DeltaScaled = int256(amountInScaled);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L872-875)
```text
        uint256 totalAvailableToken0Scaled = binTotals.scaledToken0;
        if (amountOutScaled > totalAvailableToken0Scaled) {
          amountOutScaled = totalAvailableToken0Scaled;
        }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1049-1052)
```text
        uint256 totalAvailableToken1Scaled = binTotals.scaledToken1;
        if (amountOutScaled > totalAvailableToken1Scaled) {
          amountOutScaled = totalAvailableToken1Scaled;
        }
```
