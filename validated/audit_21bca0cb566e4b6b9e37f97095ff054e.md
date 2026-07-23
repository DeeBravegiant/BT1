Now I have a clear picture of the codebase. Let me verify the exact overflow condition in `_scaleWeightsToShares` and the callback flow.

### Title
`_scaleWeightsToShares` Overflows and DoS's `addLiquidityWeighted` When `maxAmountToken0/1 = type(uint256).max` and Pool Requires Small Token Amount — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder._scaleWeightsToShares` computes a scale factor as `Math.mulDiv(max0, WAD, need0)`. When a caller passes `maxAmountToken0 = type(uint256).max` (the standard "no limit" sentinel) and the probe returns a small but non-zero `need0` (any value less than `WAD = 1e18`), the intermediate product `type(uint256).max × 1e18` exceeds `uint256` capacity and OpenZeppelin's `Math.mulDiv` reverts. This permanently DoS's `addLiquidityWeighted` for the most common usage pattern.

---

### Finding Description

The two `addLiquidityWeighted` overloads in `MetricOmmPoolLiquidityAdder` implement a probe-then-scale pattern:

1. A dry-run `addLiquidity` call is made with `KIND_PROBE` callback data.
2. The callback immediately reverts with `LiquidityProbe(amount0Delta, amount1Delta)`, carrying the exact token amounts the pool would require.
3. `_scaleWeightsToShares` uses those amounts (`need0`, `need1`) to scale the weight vector to fit within the caller's budget (`max0`, `max1`). [1](#0-0) 

The scale factor computation is: [2](#0-1) 

The intent is `scaleWad0 = max0 / need0` expressed in WAD units. The guard `need0 == 0 ? type(uint256).max` correctly handles the "pool needs no token0" case. However, there is **no guard** for the case where `need0 > 0` but `max0 × WAD / need0 > type(uint256).max`.

OpenZeppelin's `Math.mulDiv` uses 512-bit intermediate arithmetic but **reverts** when the final quotient exceeds `uint256`:

```
require(denominator > prod1);  // panics with ARITHMETIC_OVERFLOW
```

The overflow condition is: `max0 × WAD > type(uint256).max × need0`, i.e., `max0 / need0 > type(uint256).max / WAD`.

For `max0 = type(uint256).max` this simplifies to: **`need0 < WAD = 1e18`**.

**When does `need0 < 1e18` occur?**

`need0` is `amount0Added` from `LiquidityLib.addLiquidity`, converted to external (native) token units: [3](#0-2) [4](#0-3) 

For a fresh bin above the current price with `INITIAL_SCALED_TOKEN_0_PER_SHARE_E18 = 1e18` (the standard test value) and `sharesToAdd = 1`:

```
amount0Scaled = ceil(1e18 × 1 / 1e18) = 1
need0 = ceil(1 / TOKEN_0_SCALE_MULTIPLIER) = 1   (for 18-decimal token)
```

`need0 = 1 ≪ WAD = 1e18` → overflow.

This is not a contrived edge case. Any liquidity addition requiring fewer than `1e18` native units of token0 (i.e., less than 1 full token for 18-decimal assets, or less than 1 million USDC for 6-decimal assets) triggers the revert when `max0 = type(uint256).max`.

The callback flow confirms `need0` and `need1` are the actual amounts the pool would pull: [5](#0-4) 

---

### Impact Explanation

`addLiquidityWeighted` is the primary periphery entry point for budget-constrained liquidity addition. Passing `type(uint256).max` as the max cap is the idiomatic "no limit" pattern (confirmed by test usage of `type(uint256).max` for `addLiquidityExactShares`). When this pattern is used and the pool requires a small token amount — which is the normal case for any small-share deposit — the function reverts unconditionally with an arithmetic panic. Users cannot add liquidity through the weighted path; they must fall back to `addLiquidityExactShares`, which requires knowing exact share counts in advance and defeats the purpose of the weighted helper.

---

### Likelihood Explanation

- `type(uint256).max` as a "no limit" cap is standard DeFi practice and is used in the project's own test suite.
- `need0 < 1e18` is the **common case** for any deposit of less than 1 token0 (18-decimal) or less than ~1 million USDC (6-decimal). Small liquidity additions are routine.
- No special pool state, privileged role, or malicious setup is required — any unprivileged caller triggers this on any legitimate pool.

---

### Recommendation

Mirror the fix from the external report: treat an overflowing ratio as "unconstrained" (i.e., return `type(uint256).max`) rather than reverting. The simplest correct fix is a saturating check before the `mulDiv`:

```diff
// MetricOmmPoolLiquidityAdder._scaleWeightsToShares

- uint256 scaleWad0 = need0 == 0 ? type(uint256).max : Math.mulDiv(max0, WAD, need0);
- uint256 scaleWad1 = need1 == 0 ? type(uint256).max : Math.mulDiv(max1, WAD, need1);
+ uint256 scaleWad0 = (need0 == 0 || max0 >= type(uint256).max / WAD)
+     ? type(uint256).max
+     : Math.mulDiv(max0, WAD, need0);
+ uint256 scaleWad1 = (need1 == 0 || max1 >= type(uint256).max / WAD)
+     ? type(uint256).max
+     : Math.mulDiv(max1, WAD, need1);
```

The condition `max0 >= type(uint256).max / WAD` (≈ `1.15 × 10^59`) is a safe proxy for "the ratio would overflow"; at that scale the caller is effectively unconstrained and returning `type(uint256).max` is semantically correct. Alternatively, use a saturating `mulDiv` helper that caps at `type(uint256).max` instead of reverting.

---

### Proof of Concept

```solidity
// Foundry test — add to MetricOmmPoolLiquidityAdder.t.sol

function test_addLiquidityWeighted_overflowsWhenMaxIsUnboundedAndNeedIsSmall() public {
    // Single bin above current price; weight = 1 share
    LiquidityDelta memory w;
    w.binIdxs = new int256[](1);
    w.shares  = new uint256[](1);
    w.binIdxs[0] = 4;   // above current bin (0)
    w.shares[0]  = 1;   // minimal weight

    (int8 lo,, int8 hi,) = _unconstrainedCursorBounds();

    vm.prank(alice);
    // type(uint256).max as "no limit" — standard usage
    // Probe will return need0 = 1 (1 wei of token0 for 1 share in empty bin)
    // _scaleWeightsToShares: Math.mulDiv(type(uint256).max, 1e18, 1) → ARITHMETIC_OVERFLOW
    vm.expectRevert();
    helper.addLiquidityWeighted(
        address(pool),
        alice,
        /*salt*/ 99,
        w,
        type(uint256).max,   // maxAmountToken0 — triggers overflow
        type(uint256).max,   // maxAmountToken1
        lo, 0, hi, type(uint104).max,
        ""
    );
}
```

The probe fires `LiquidityProbe(1, 0)` (1 wei token0, 0 token1 for a single share in a fresh above-price bin). `_scaleWeightsToShares` then executes `Math.mulDiv(type(uint256).max, 1e18, 1)`, which panics with `ARITHMETIC_OVERFLOW` before any state is written. [6](#0-5)

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L106-114)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L157-158)
```text
    if (kind == KIND_PROBE) {
      revert LiquidityProbe(amount0Delta, amount1Delta);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L226-243)
```text
  function _scaleWeightsToShares(LiquidityDelta calldata w, uint256 max0, uint256 max1, uint256 need0, uint256 need1)
    internal
    pure
    returns (LiquidityDelta memory out)
  {
    uint256 scaleWad0 = need0 == 0 ? type(uint256).max : Math.mulDiv(max0, WAD, need0);
    uint256 scaleWad1 = need1 == 0 ? type(uint256).max : Math.mulDiv(max1, WAD, need1);
    uint256 scaleWad = scaleWad0 < scaleWad1 ? scaleWad0 : scaleWad1;

    uint256 n = w.binIdxs.length;
    out.binIdxs = new int256[](n);
    out.shares = new uint256[](n);
    for (uint256 i; i < n; i++) {
      out.binIdxs[i] = w.binIdxs[i];
      out.shares[i] = Math.mulDiv(w.shares[i], scaleWad, WAD);
      if (w.shares[i] != 0 && out.shares[i] == 0) revert SharesRoundedToZero();
    }
  }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L87-89)
```text
              amount1Scaled = Math.ceilDiv(_checkedMul(ctx.initialScaledToken1PerShareE18, sharesToAdd), 1e18);
            } else if (binIdx > curBinIdxCache) {
              amount0Scaled = Math.ceilDiv(_checkedMul(ctx.initialScaledToken0PerShareE18, sharesToAdd), 1e18);
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L141-142)
```text
      (amount0Added, amount1Added) =
        _deltasScaledToExternal(totalToken0ToAddScaled, totalToken1ToAddScaled, ctx, Math.Rounding.Ceil);
```
