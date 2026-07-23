### Title
`swap()` Silently Succeeds with Zero Output When No Liquidity Is Available — (`metric-core/contracts/MetricOmmPool.sol`)

### Summary

`MetricOmmPool.swap()` does not revert when the swap produces zero output due to exhausted liquidity or a price limit already being hit. The transaction succeeds, the callback is invoked with `(0, 0)`, a `Swap` event is emitted with zero amounts, and `(0, 0)` is returned — exactly mirroring the Liquity "redemption without redeemable troves" pattern.

### Finding Description

When `swap()` is called, it delegates to one of four internal helpers depending on direction and exact-in/out mode. Each helper contains an early-exit path that returns `(0, 0, 0[, 0])` without reverting:

**`_swapToken0ForToken1SpecifiedInput`** — price limit already satisfied: [1](#0-0) 

**`_swapToken1ForToken0SpecifiedInput`** — price limit already satisfied: [2](#0-1) 

**`_swapToken0ForToken1SpecifiedOutput`** — requested output silently capped to zero when `binTotals.scaledToken1 == 0`: [3](#0-2) 

**`_swapToken1ForToken0SpecifiedOutput`** — same cap for token0: [4](#0-3) 

All four paths return `(0, 0, 0)` / `(0, 0, 0, 0)` to `_executeSwap`, which propagates `amount0DeltaScaled = 0`, `amount1DeltaScaled = 0` back to `swap()`. [5](#0-4) 

In `swap()`, the consequence is:

1. No tokens are transferred to `recipient` (both `amount1Delta < 0` and `amount0Delta < 0` are false).
2. `IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(0, 0, callbackData)` is called — the caller pays nothing.
3. The `IncorrectDelta` guard is skipped because `amount0Delta > 0` is false.
4. `emit Swap(...)` fires with zero amounts.
5. `(0, 0)` is returned to the caller.

The only guard against a zero-amount swap is `require(amountSpecified != 0, InvalidAmount())` at the entry point — but this checks the *requested* amount, not the *executed* amount. [6](#0-5) 

### Impact Explanation

Any router, aggregator, or user calling `swap()` with a non-zero `amountSpecified` receives a successful transaction and a `(0, 0)` return value when the pool has no liquidity in the requested direction or when the price limit is already satisfied. The caller has no on-chain signal distinguishing "swap executed" from "swap silently did nothing." Downstream logic in routers that assumes a successful `swap()` call delivered tokens will proceed incorrectly. The `Swap` event emitted with zero amounts provides no useful diagnostic.

This is broken core pool functionality: the swap flow accepts a valid non-zero input request, executes no state change, and returns success — matching the Sherlock criterion of "broken core pool functionality causing unusable swap flows."

### Likelihood Explanation

This is reachable under normal operating conditions:
- A pool that has been fully drained of one token (all token1 removed by LPs) will silently accept any `zeroForOne = true` swap.
- A caller passing a `priceLimitX64` that is already satisfied by the current oracle price triggers the early-exit on every swap call.
- Both conditions are unprivileged and require no malicious setup.

### Recommendation

After `_executeSwap` returns, add a check that reverts if both deltas are zero and `amountSpecified != 0`:

```solidity
if (amount0Delta == 0 && amount1Delta == 0) revert SwapResultedInZeroOutput();
```

This mirrors the Liquity recommendation: a descriptive revert when the operation produces no effect, rather than a silent success.

### Proof of Concept

1. Deploy a pool with token0/token1 and add liquidity only to token0-side bins (no token1 in any bin, `binTotals.scaledToken1 == 0`).
2. Call `swap(recipient, true, 1e18, 0, callbackData, "")` — exact-input, selling token0 for token1.
3. Inside `_swapToken0ForToken1SpecifiedInput`, `totalAvailableToken1Scaled == 0` causes the loop to break immediately after the price-limit check passes.
4. `_executeSwap` returns `(0, 0, 0)`.
5. `swap()` calls `metricOmmSwapCallback(0, 0, callbackData)` — caller pays nothing.
6. `emit Swap(msg.sender, recipient, true, 0, 0, curBinIdx, curPosInBin, 0)` fires.
7. `swap()` returns `(0, 0)` — transaction succeeds.
8. Recipient received 0 token1; caller paid 0 token0. The swap silently did nothing. [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-301)
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

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );

    (uint256 midPriceX64, uint256 baseFeeX64) =
      SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    SwapMath.InternalSwapParams memory params =
      SwapMath.InternalSwapParams({midPriceX64: midPriceX64, baseFeeX64: baseFeeX64, priceLimitX64: priceLimitX64});

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

    uint256 packedSlot0Final = Slot0Library.loadPackedSlot0();
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );

    emit Swap(
      msg.sender, recipient, amountSpecified > 0, amount0Delta, amount1Delta, curBinIdx, curPosInBin, protocolFeeAmount
    );
    return (amount0Delta.toInt128(), amount1Delta.toInt128());
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

**File:** metric-core/contracts/MetricOmmPool.sol (L1132-1216)
```text
  function _swapToken0ForToken1SpecifiedInput(uint256 amountInScaled, SwapMath.InternalSwapParams memory params)
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
      ) = _getInitialStateForSwap(true, false, params, amountInScaled);

      if (params.priceLimitX64 >= initialPriceX64) {
        return (0, 0, 0);
      }

      uint256 totalAvailableToken1Scaled = binTotals.scaledToken1;

      while (state.amountSpecifiedRemainingScaled > 0) {
        bool nonEmptyBin = true;
        if (binState.token1BalanceScaled == 0 || curPosInBinCache == 0) {
          if (params.priceLimitX64 != 0 && params.priceLimitX64 >= lowerPriceX64) {
            break;
          }
          if (totalAvailableToken1Scaled == 0) {
            break;
          }
          nonEmptyBin = false;
        }

        if (nonEmptyBin) {
          uint256 outToken1AmountScaled;
          int256 delta0Scaled;
          int256 delta1Scaled;
          uint256 binLpFeeAmountScaled;

          (curPosInBinCache, outToken1AmountScaled, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) =
            SwapMath.buyToken1InBinSpecifiedIn(
              binState,
              curPosInBinCache,
              state,
              params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6),
              lowerPriceX64,
              upperPriceX64,
              params.priceLimitX64,
              spreadFeeE6
            );

          emit BinSwapped(
            curBinIdxCache,
            BinBalanceDelta({delta0Scaled: delta0Scaled, delta1Scaled: delta1Scaled}),
            binLpFeeAmountScaled
          );
          _saveBinState(curBinIdxCache, binState);
          totalAvailableToken1Scaled -= outToken1AmountScaled;
        }

        if (curPosInBinCache == 0 || !nonEmptyBin) {
          if (curBinIdxCache <= LOWEST_BIN) {
            break;
          }
          curBinIdxCache--;
          binState = _binStates[curBinIdxCache];
          curPosInBinCache = type(uint104).max;
          curBinDistE6Cache -= int24(uint24(binState.lengthE6));

          upperPriceX64 = lowerPriceX64;
          lowerPriceX64 = distanceE6ToPriceX64(curBinDistE6Cache, params.midPriceX64);
        } else {
          break;
        }
      }

      _finalizeSwap(curBinIdxCache, curPosInBinCache, curBinDistE6Cache);

      return (
        amountInScaled - state.amountSpecifiedRemainingScaled,
        state.amountCalculatedScaled,
        state.protocolFeeAmountScaled
      );
    }
```
