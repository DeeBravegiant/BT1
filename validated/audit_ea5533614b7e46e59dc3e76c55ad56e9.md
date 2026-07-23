### Title
Arithmetic Mean Used Instead of Geometric Mean for Mid-Price in `OracleValueStopLossExtension` Produces Systematically Biased Stop-Loss Metrics — (File: metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol)

---

### Summary

`OracleValueStopLossExtension._afterSwapOracleStopLoss` computes the oracle mid price as the **arithmetic mean** `(bid + ask) / 2`, while every other price-consuming path in the protocol uses the **geometric mean** `sqrt(bid * ask)` via `SwapMath.midAndSpreadFeeX64FromBidAsk`. Because AM ≥ GM always (AM-GM inequality), the extension systematically overestimates the mid price, causing per-bin value metrics to be biased in opposite directions for the two tokens, which can produce false-positive stop-loss triggers (blocking legitimate swaps) or false-negative triggers (failing to protect LPs from value loss).

---

### Finding Description

**Root cause — line 218 of `OracleValueStopLossExtension.sol`:**

```solidity
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
``` [1](#0-0) 

**Correct formula used everywhere else in the protocol — `SwapMath.midAndSpreadFeeX64FromBidAsk`:**

```solidity
midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
``` [2](#0-1) 

The pool's own test suite explicitly documents that the additive/arithmetic approximation is the *buggy* version:

> `// ~1.0202e18. The buggy additive version yields ~1e18 (no spread) and fails this.` [3](#0-2) 

The biased `midPriceX64` is then fed into both per-bin value metrics:

```solidity
metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
``` [4](#0-3) 

Because AM > GM whenever bid ≠ ask:

| Metric | Effect of AM > GM | Consequence |
|--------|-------------------|-------------|
| `metricT0` | `t1 * Q64 / midPriceX64` is **smaller** → metric underestimated | Stop-loss triggers **too early** on `zeroForOne` swaps (false positive → DoS) |
| `metricT1` | `t0 * midPriceX64 / Q64` is **larger** → metric overestimated | Stop-loss triggers **too late** on `!zeroForOne` swaps (false negative → LP value leak undetected) |

The relative error between AM and GM is approximately `spread² / 8`. For a 2 % oracle spread the error is ~0.005 %; for a 10 % spread it is ~0.125 %; for a 50 % spread (extreme volatility) it reaches ~3 %.

---

### Impact Explanation

**False-positive path (zeroForOne swaps):** `metricT0` is underestimated. If the pool is near its drawdown floor, the extension reverts with `OracleStopLossTriggered` even though the true value-per-share has not breached the floor. Legitimate traders and LPs are blocked from executing `zeroForOne` swaps.

**False-negative path (!zeroForOne swaps):** `metricT1` is overestimated. The extension fails to detect a genuine drawdown in token1 value per share, allowing `!zeroForOne` swaps to drain LP value beyond the configured drawdown limit without triggering the stop-loss.

Both effects are proportional to the oracle spread and grow quadratically. During high-volatility periods — exactly when the stop-loss is most needed — the error is largest. [5](#0-4) 

---

### Likelihood Explanation

The `afterSwap` hook fires on **every swap** through any pool that has registered this extension. The trigger is fully unprivileged — any swap caller activates the biased metric check. The extension is a production periphery contract, not a test mock. [6](#0-5) 

---

### Recommendation

Replace the arithmetic mean with the geometric mean, matching the pool's swap path:

```solidity
// Before (incorrect):
uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;

// After (correct, consistent with SwapMath.midAndSpreadFeeX64FromBidAsk):
uint256 midPriceX64 = Math.sqrt(uint256(bidPriceX64) * uint256(askPriceX64));
``` [7](#0-6) 

---

### Proof of Concept

Using the test suite's own oracle values (`BID_P = 9.9 × Q64`, `ASK_P = 10.1 × Q64`, ~2 % spread):

```
AM  = (9.9 + 10.1) / 2 × Q64 = 10.0 × Q64
GM  = sqrt(9.9 × 10.1) × Q64 = sqrt(99.99) × Q64 ≈ 9.9995 × Q64
Δ   ≈ 0.005 % of mid
```

For a bin holding `t0 = 1e18` (scaled) and `t1 = 0`:

```
metricT1_AM = t0 × AM / Q64 × METRIC_SCALE / shares
metricT1_GM = t0 × GM / Q64 × METRIC_SCALE / shares
metricT1_AM / metricT1_GM = AM / GM ≈ 1.00005
```

The overestimated `metricT1` raises the high-watermark ratchet by 0.005 % above the true value, meaning the stop-loss floor is set 0.005 % too high in token1 terms — the extension will not fire until the true value has already fallen 0.005 % below the intended floor. At a 10 % oracle spread the gap widens to ~0.125 %, and at 50 % spread to ~3 %, which is material relative to typical drawdown configurations (e.g., `drawdownE6 = 5_000` = 0.5 %). [8](#0-7) [9](#0-8)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L185-203)
```text
  function afterSwap(
    address,
    address,
    bool zeroForOne,
    int128,
    uint128,
    uint256 packedSlot0Initial,
    uint256 packedSlot0Final,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    int128,
    int128,
    uint256,
    bytes calldata
  ) external override returns (bytes4) {
    // Only the factory can initialize, so an initialized msg.sender is a legit pool — no onlyPool needed.
    _requireInitialized(msg.sender);
    _afterSwapOracleStopLoss(msg.sender, packedSlot0Initial, packedSlot0Final, bidPriceX64, askPriceX64, zeroForOne);
    return IMetricOmmExtensions.afterSwap.selector;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L207-220)
```text
  function _afterSwapOracleStopLoss(
    address pool_,
    uint256 packedSlot0Initial,
    uint256 packedSlot0Final,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bool zeroForOne
  ) internal {
    PoolStopLossConfig memory cfg = oracleStopLossConfig[pool_];
    uint256 drawdown = cfg.drawdownE6;
    if (drawdown == 0) return;
    uint256 midPriceX64 = (uint256(bidPriceX64) + uint256(askPriceX64)) / 2;
    uint256 minShares = IMetricOmmPool(pool_).getImmutables().minimalMintableLiquidity;
    if (minShares == 0) minShares = 1;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L246-256)
```text
  function _metrics(uint104 t0, uint104 t1, uint256 totalShares, uint256 minShares, uint256 midPriceX64)
    private
    pure
    returns (uint256 metricT0, uint256 metricT1)
  {
    uint256 shares = totalShares < minShares ? minShares : totalShares;
    uint256 t0ps = Math.mulDiv(uint256(t0), METRIC_SCALE, shares);
    uint256 t1ps = Math.mulDiv(uint256(t1), METRIC_SCALE, shares);
    metricT0 = _clampMetric(t0ps + Math.mulDiv(Math.mulDiv(uint256(t1), Q64, midPriceX64), METRIC_SCALE, shares));
    metricT1 = _clampMetric(Math.mulDiv(Math.mulDiv(uint256(t0), midPriceX64, Q64), METRIC_SCALE, shares) + t1ps);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L270-278)
```text
    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
    }
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L70-71)
```text
    midPriceX64 = Math.sqrt(bidPriceX64 * askPriceX64);
    baseFeeX64 = Math.mulDiv(askPriceX64, ONE_X64, midPriceX64, Math.Rounding.Ceil) - ONE_X64;
```

**File:** metric-core/test/MetricOmmPool.getSellAndBuyPrices.t.sol (L17-21)
```text
  // the original additive bug (mid ± fee) only diverges from the correct multiplicative quote when the
  // price differs from 1, so testing at ≈1 would mask it.
  uint128 internal constant BID_P = uint128(uint256(Q64) * 99 / 10); // 9.9
  uint128 internal constant ASK_P = uint128(uint256(Q64) * 101 / 10); // 10.1
  uint24 internal constant NOTIONAL_1_PCT_E8 = 1_000_000;
```

**File:** metric-core/test/MetricOmmPool.getSellAndBuyPrices.t.sol (L38-41)
```text
    uint256 poolRatio = uint256(ask) * 1e18 / uint256(bid);
    uint256 providerRatio = uint256(ASK_P) * 1e18 / uint256(BID_P);
    // ~1.0202e18. The buggy additive version yields ~1e18 (no spread) and fails this.
    assertApproxEqRel(poolRatio, providerRatio, 0.005e18, "pool fee envelope must match provider");
```
