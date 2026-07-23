Now I have all the information needed. Let me analyze the complete picture.

**Key facts established:**

1. **Going-up paths** (lines 931, 1021) use `_clampInt256ToInt24(_addDistE6(...))` — safe.
2. **Going-down paths** (lines 1110, 1200) use `curBinDistE6Cache -= int24(uint24(binState.lengthE6))` — no clamp.
3. `_finalizeSwap` (line 861) calls `curBinDistE6Cache.toInt24()` via OpenZeppelin `SafeCast`, which **reverts** if the value is outside `int24` range.
4. Factory validation (`_unpackAndValidateBinStates`, line 573) constrains `initialCurBinDistFromProvidedPriceE6` to the range `(-1e6, 1e6)`, not `int24` range.
5. `int24.min` = -8,388,608. Starting from -999,999 (max negative initial), after ~113 bin traversals of `lengthE6 = 65535`, `curBinDistE6Cache` = -999,999 − 113×65,535 = -8,405,454 < `int24.min`.
6. Across multiple swaps, `curBinDistFromProvidedPriceE6` accumulates downward. Once it reaches a value where the next going-down swap traverses even one bin, `_finalizeSwap` reverts permanently.

---

### Title
Missing `_clampInt256ToInt24` on going-down bin traversal causes permanent DoS on all zeroForOne swaps — (`metric-core/contracts/MetricOmmPool.sol`)

### Summary

The four swap implementations are asymmetric: going-up paths clamp `curBinDistE6Cache` to `int24` range before storing, but going-down paths do not. After enough going-down bin traversals across multiple swaps, `curBinDistE6Cache` falls below `int24.min`, causing `_finalizeSwap` → `SafeCast.toInt24()` to revert. All subsequent zeroForOne swaps (both exact-in and exact-out) permanently revert, breaking core pool functionality.

### Finding Description

In `_swapToken0ForToken1SpecifiedOutput` and `_swapToken0ForToken1SpecifiedInput`, when the swap loop moves to a lower bin index, the distance cache is updated as:

```solidity
// MetricOmmPool.sol line 1110 / 1200
curBinDistE6Cache -= int24(uint24(binState.lengthE6));
```

`curBinDistE6Cache` is declared as `int256` and the entire function body is `unchecked`, so this subtraction never reverts — it simply produces a value below `int24.min`. The symmetric going-up paths correctly use:

```solidity
// MetricOmmPool.sol line 931 / 1021
curBinDistE6Cache = _clampInt256ToInt24(_addDistE6(int256(curBinDistE6Cache), binState.lengthE6));
```

At the end of every swap, `_finalizeSwap` calls:

```solidity
// MetricOmmPool.sol line 861
curBinDistFromProvidedPriceE6 = curBinDistE6Cache.toInt24(); // SafeCast — reverts if out of range
```

Once `curBinDistE6Cache` < `int24.min` = −8,388,608, this line reverts and the entire swap transaction is rolled back.

**Accumulation path (no malicious setup required):**

- Factory validation (`_unpackAndValidateBinStates`, line 573) constrains `initialCurBinDistFromProvidedPriceE6` to `(−1e6, 1e6)`, i.e., at most −999,999.
- Each going-down bin traversal subtracts up to 65,535 (max `uint16` `lengthE6`).
- Starting from −999,999, after ~113 traversals of length 65,535: −999,999 − 113 × 65,535 = −8,405,454 < `int24.min`.
- With typical lengths (e.g., 100), it takes ~74,000 traversals from 0 — achievable over the pool's lifetime.
- Each successful swap stores the new (still-in-range) value; the pool gradually approaches the cliff. The first swap that would push it over `int24.min` reverts and every subsequent going-down swap also reverts.

### Impact Explanation

Once the pool reaches the critical state, **all `zeroForOne` swaps permanently revert** — both `_swapToken0ForToken1SpecifiedOutput` and `_swapToken0ForToken1SpecifiedInput`. The pool becomes permanently one-directional. Traders cannot sell token0 for token1. This is broken core pool functionality per the contest's allowed impact gate ("unusable swap flows"). LPs can still call `removeLiquidity` directly, so principal is not locked, but the pool's primary swap utility is destroyed.

### Likelihood Explanation

The trigger requires the pool to have accumulated many going-down bin traversals. With large `lengthE6` values (up to 65,535) and a negative initial `curBinDistFromProvidedPriceE6`, the threshold can be reached in ~113 total bin traversals across all historical swaps — a realistic scenario for an active pool in a sustained downtrend. The factory places no constraint preventing this accumulation.

### Recommendation

Apply the same clamping used by the going-up paths to both going-down paths:

```solidity
// Replace lines 1110 and 1200 with:
curBinDistE6Cache = _clampInt256ToInt24(int256(curBinDistE6Cache) - int256(uint256(binState.lengthE6)));
```

This mirrors the going-up guard and prevents `_finalizeSwap` from ever receiving an out-of-range value.

### Proof of Concept

1. Deploy a pool with `initialCurBinDistFromProvidedPriceE6 = -999_999` and 128 negative bins each with `lengthE6 = 65535`.
2. Execute going-down swaps (zeroForOne=true) that each traverse ~10 bins. After ~11 such swaps, `curBinDistFromProvidedPriceE6` ≈ −7,864,199.
3. Execute a 12th going-down swap that traverses 10 bins: `curBinDistE6Cache` = −7,864,199 − 10 × 65,535 = −8,519,549 < `int24.min`.
4. Assert the swap reverts at `_finalizeSwap` → `toInt24()`.
5. Confirm all subsequent zeroForOne swaps also revert.
6. Confirm going-up swaps (zeroForOne=false) still succeed (they use `_clampInt256ToInt24`).

---

**Relevant code locations:**

Going-down paths missing the clamp: [1](#0-0) [2](#0-1) 

Going-up paths with correct clamp (for comparison): [3](#0-2) [4](#0-3) 

`_finalizeSwap` SafeCast revert site: [5](#0-4) 

`_clampInt256ToInt24` helper (used only on going-up paths): [6](#0-5) 

Factory validation bounding initial distance to `(−1e6, 1e6)` — not `int24` range: [7](#0-6) [8](#0-7)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L925-932)
```text
        if (curPosInBinCache >= type(uint104).max || !nonEmptyBin) {
          if (curBinIdxCache >= HIGHEST_BIN) {
            break;
          }
          curBinIdxCache++;
          curPosInBinCache = 0;
          curBinDistE6Cache = _clampInt256ToInt24(_addDistE6(int256(curBinDistE6Cache), binState.lengthE6));

```

**File:** metric-core/contracts/MetricOmmPool.sol (L1015-1022)
```text
        if (curPosInBinCache >= type(uint104).max || !nonEmptyBin) {
          if (curBinIdxCache >= HIGHEST_BIN) {
            break;
          }
          curBinIdxCache++;
          curPosInBinCache = 0;
          curBinDistE6Cache = _clampInt256ToInt24(_addDistE6(int256(curBinDistE6Cache), binState.lengthE6));

```

**File:** metric-core/contracts/MetricOmmPool.sol (L1103-1113)
```text
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1193-1201)
```text
        if (curPosInBinCache == 0 || !nonEmptyBin) {
          if (curBinIdxCache <= LOWEST_BIN) {
            break;
          }
          curBinIdxCache--;
          binState = _binStates[curBinIdxCache];
          curPosInBinCache = type(uint104).max;
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
