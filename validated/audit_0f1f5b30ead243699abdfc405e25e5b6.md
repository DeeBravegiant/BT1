Audit Report

## Title
Arithmetic Mean Used Instead of Geometric Mean for Mid-Price in `_afterSwapOracleStopLoss` Produces Systematically Biased Stop-Loss Metrics — (File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol)

## Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid price as the arithmetic mean `(bidPriceX64 + askPriceX64) / 2` at line 218, while every other price-consuming path in the protocol uses the geometric mean `sqrt(bid * ask)` via `SwapMath.midAndSpreadFeeX64FromBidAsk`. Because AM ≥ GM always (AM-GM inequality), the extension systematically overestimates the mid price, causing per-bin value metrics to be biased in opposite directions for the two tokens, which can produce false-positive stop-loss triggers (blocking legitimate swaps) or false-negative triggers (failing to protect LPs from value loss).

## Finding Description

**Root cause — line 218 of `OracleValueStopLossExtension.sol`:**
```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
```

**Correct formula used everywhere else in the protocol — `SwapMath.midAndSpreadFeeX64FromBidAsk` (line 70 of `SwapMath.sol`):**
```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
```

This geometric mean is used consistently in `MetricOmmPool.getSellAndBuyPrices` (line 530) and `MetricOmmPoolDataProvider._marginalBestBidAsk` (line 283). The biased `midPriceX64` is then fed into both per-bin value metrics at lines 254–255:

```solidity
metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
```

Because AM > GM whenever bid ≠ ask:
- `metricT0`: `t1 * Q64 / midPriceX64` is **smaller** → metric underestimated → stop-loss triggers too early on `zeroForOne` swaps (false positive → DoS of legitimate swaps)
- `metricT1`: `t0 * midPriceX64 / Q64` is **larger** → metric overestimated → stop-loss triggers too late on `!zeroForOne` swaps (false negative → LP value leak undetected)

Critically, the bias does **not** cancel out across watermark updates: the watermark ratchet is set at one oracle spread, and the breach check occurs at a potentially different spread. If the spread widens between watermark setting and the next check, `metricT0` is more underestimated than the watermark was, causing a false-positive revert; `metricT1` is more overestimated, masking a genuine drawdown.

The relative error between AM and GM is approximately `spread² / 8`. For a 2% oracle spread the error is ~0.005%; for a 10% spread it is ~0.125%; for a 50% spread it reaches ~3%.

## Impact Explanation

**False-positive path (zeroForOne swaps):** `metricT0` is underestimated. If the pool is near its drawdown floor, the extension reverts with `OracleStopLossTriggered` even though the true value-per-share has not breached the floor. Legitimate traders and LPs are blocked from executing `zeroForOne` swaps — broken core swap functionality.

**False-negative path (!zeroForOne swaps):** `metricT1` is overestimated. The extension fails to detect a genuine drawdown in token1 value per share, allowing `!zeroForOne` swaps to drain LP value beyond the configured drawdown limit without triggering the stop-loss — direct loss of LP principal.

At a 10% oracle spread the gap is ~0.125%, and at 50% spread ~3%, which is material relative to typical drawdown configurations (e.g., `drawdownE6 = 5_000` = 0.5%). Both effects are largest during high-volatility periods — exactly when the stop-loss protection is most needed.

## Likelihood Explanation

The `afterSwap` hook fires on every swap through any pool that has registered this extension. The trigger is fully unprivileged — any swap caller activates the biased metric check. The extension is a production periphery contract. The bias is always present whenever bid ≠ ask (i.e., whenever the oracle has any spread), and grows with market volatility.

## Recommendation

Replace the arithmetic mean with the geometric mean, matching the pool's swap path and all other protocol price consumers:

```solidity
// Before (incorrect):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct, consistent with SwapMath.midAndSpreadFeeX64FromBidAsk):
uint256 midPriceX64 = Math.sqrt(uint256(bidPriceX64) * uint256(askPriceX64));
```

## Proof of Concept

Using oracle values with a ~2% spread (`BID_P = 9.9 × Q64`, `ASK_P = 10.1 × Q64`):

```
AM  = (9.9 + 10.1) / 2 × Q64 = 10.0 × Q64
GM  = sqrt(9.9 × 10.1) × Q64 = sqrt(99.99) × Q64 ≈ 9.9995 × Q64
Δ   ≈ 0.005% of mid
```

Scenario: watermark set at spread=2% (AM=10.0), then oracle spread widens to 10% (`BID=9.5×Q64`, `ASK=10.5×Q64`):
- New AM = 10.0×Q64, New GM = sqrt(99.75)×Q64 ≈ 9.9875×Q64
- For a bin holding only token0: `metricT1_AM / metricT1_GM ≈ 1.00125`
- The overestimated `metricT1` at check time means the stop-loss floor is effectively 0.125% higher than the watermark-implied floor — the extension will not fire until the true value has already fallen 0.125% below the intended floor.

A Foundry test can reproduce this by: (1) calling `_exposeStopLoss` with a narrow spread to set the watermark, (2) calling again with a wider spread and a bin value just below the true drawdown floor, and asserting that `OracleStopLossTriggered` is not emitted (false negative) or is incorrectly emitted (false positive) depending on the direction.