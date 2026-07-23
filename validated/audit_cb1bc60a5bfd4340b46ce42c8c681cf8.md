Audit Report

## Title
Missing `_clampInt256ToInt24` on going-down bin traversal causes permanent DoS on all zeroForOne swaps — (`metric-core/contracts/MetricOmmPool.sol`)

## Summary

The four swap implementations are asymmetric: going-up paths clamp `curBinDistE6Cache` to `int24` range before storing, but going-down paths do not. After enough going-down bin traversals across multiple swaps, `curBinDistE6Cache` falls below `int24.min`, causing `_finalizeSwap` → `SafeCast.toInt24()` to revert. All subsequent zeroForOne swaps permanently revert, breaking core pool swap functionality.

## Finding Description

In `_swapToken0ForToken1SpecifiedOutput` and `_swapToken0ForToken1SpecifiedInput`, both declared `int256 curBinDistE6Cache` inside `unchecked` blocks, the going-down bin traversal updates the distance cache without clamping: [1](#0-0) [2](#0-1) 

The symmetric going-up paths in `_swapToken1ForToken0SpecifiedOutput` and `_swapToken1ForToken0SpecifiedInput` correctly apply a clamp: [3](#0-2) [4](#0-3) 

At the end of every swap, `_finalizeSwap` calls `SafeCast.toInt24()` on the cache value: [5](#0-4) 

This reverts if `curBinDistE6Cache` is outside `[-8,388,608, 8,388,607]`. Because the subtraction is `int256 -= int24` (promoted to `int256 -= int256`) inside `unchecked`, it silently produces values below `int24.min` without reverting at the subtraction site.

The factory validation only constrains `initialCurBinDistFromProvidedPriceE6` to `(-1e6, 1e6)` at pool creation: [6](#0-5) [7](#0-6) 

This is far narrower than `int24` range and places no constraint on runtime accumulation. Each successful swap stores a value within `int24` range (otherwise it would have reverted), so `curBinDistFromProvidedPriceE6` drifts downward across multiple swaps until a single swap's traversal pushes `curBinDistE6Cache` below `int24.min`, causing permanent revert.

The helper `_clampInt256ToInt24` exists and is used only on going-up paths: [8](#0-7) 

## Impact Explanation

Once `curBinDistFromProvidedPriceE6` reaches a value where the next going-down swap traverses even one bin and pushes the cache below `int24.min`, `_finalizeSwap` reverts permanently. All `zeroForOne` swaps — both `_swapToken0ForToken1SpecifiedOutput` and `_swapToken0ForToken1SpecifiedInput` — become permanently unusable. This is broken core pool functionality (unusable swap flow) per the contest's allowed impact gate.

## Likelihood Explanation

No privileged setup is required. Normal trading activity in a sustained downtrend accumulates the distance. Starting from the maximum negative initial value of −999,999, with bins of `lengthE6 = 65,535` (max `uint16`), only ~113 total bin traversals across all historical swaps are needed to reach `int24.min`. With typical smaller bin lengths the threshold is higher but still reachable over a pool's lifetime. Any unprivileged trader executing zeroForOne swaps contributes to the accumulation.

## Recommendation

Apply the same clamping used by the going-up paths to both going-down paths at lines 1110 and 1200:

```solidity
// Replace:
curBinDistE6Cache -= int24(uint24(binState.lengthE6));
// With:
curBinDistE6Cache = _clampInt256ToInt24(int256(curBinDistE6Cache) - int256(uint256(binState.lengthE6)));
```

This mirrors the going-up guard and prevents `_finalizeSwap` from ever receiving an out-of-range value.

## Proof of Concept

1. Deploy a pool with `initialCurBinDistFromProvidedPriceE6 = -999_999` and 128 negative bins each with `lengthE6 = 65535`.
2. Execute going-down swaps (zeroForOne=true) that each traverse ~10 bins. After ~11 such swaps, `curBinDistFromProvidedPriceE6` ≈ −8,208,849 (within `int24` range, stored successfully).
3. Execute a 12th going-down swap traversing 10 bins: `curBinDistE6Cache` = −8,208,849 − 10 × 65,535 = −8,864,199 < `int24.min` = −8,388,608.
4. Assert the swap reverts at `_finalizeSwap` → `toInt24()`.
5. Confirm all subsequent zeroForOne swaps also revert.
6. Confirm going-up swaps (zeroForOne=false) still succeed via `_clampInt256ToInt24`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L589-597)
```text
  function _clampInt256ToInt24(int256 v) internal pure returns (int24) {
    unchecked {
      if (v > type(int24).max) return type(int24).max;
      if (v < type(int24).min) return type(int24).min;
      // casting to int24 is safe because values outside int24 bounds are clamped above.
      // forge-lint: disable-next-line(unsafe-typecast)
      return int24(v);
    }
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L858-862)
```text
  function _finalizeSwap(int256 curBinIdxCache, uint256 curPosInBinCache, int256 curBinDistE6Cache) internal {
    curBinIdx = curBinIdxCache.toInt8();
    curPosInBin = curPosInBinCache.toUint104();
    curBinDistFromProvidedPriceE6 = curBinDistE6Cache.toInt24();
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L931-931)
```text
          curBinDistE6Cache = _clampInt256ToInt24(_addDistE6(int256(curBinDistE6Cache), binState.lengthE6));
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1021-1021)
```text
          curBinDistE6Cache = _clampInt256ToInt24(_addDistE6(int256(curBinDistE6Cache), binState.lengthE6));
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1110-1110)
```text
          curBinDistE6Cache -= int24(uint24(binState.lengthE6));
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1200-1200)
```text
          curBinDistE6Cache -= int24(uint24(binState.lengthE6));
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L572-573)
```text
    int256 cumulativeDistance = int256(curBinDistFromProvidedPriceE6);
    if (cumulativeDistance >= 1e6 || cumulativeDistance <= -1e6) revert BinDistanceOutOfRange(0, cumulativeDistance);
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L607-609)
```text
        cumulativeDistance -= length.toInt256();
        if (cumulativeDistance <= -1e6) {
          revert BinDistanceOutOfRange(-negBinCount - 1, cumulativeDistance);
```
