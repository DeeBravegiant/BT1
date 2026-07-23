### Title
Scaled-to-native rounding deficit in `_executeSwap` causes permanent `binTotals.scaledToken1` over-accounting, leading to LP insolvency — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

In `_executeSwap`, `binTotals.scaledToken1` is decremented by the full raw `amountOutScaled` before the scaled-to-native conversion. The conversion via `deltasScaledToExternal` applies `SignedMath.ceilDiv` to a negative value, which rounds toward zero (i.e., floor division on the magnitude), so the pool transfers fewer native tokens than the scaled deduction implies. The remainder (`amountOutScaled % TOKEN_1_SCALE_MULTIPLIER`) is permanently lost from the pool's real balance but remains charged against `binTotals.scaledToken1`. This deficit accumulates across every exact-input swap and eventually makes `binTotals.scaledToken1 > balance1() * TOKEN_1_SCALE_MULTIPLIER`, rendering the pool insolvent for LPs.

---

### Finding Description

**Step 1 — `binTotals.scaledToken1` is decremented by the full raw scaled output.**

In `_executeSwap`, for `zeroForOne = true` (exact-input):

```
amount1DeltaScaled = -int256(amountOutScaled)   // e.g. -1_500_000
binTotals.scaledToken1 -= uint256(-amount1DeltaScaled)  // decremented by 1_500_000
``` [1](#0-0) 

**Step 2 — The external delta is computed with `ceilDiv` on a negative value.**

```solidity
function deltasScaledToExternal(int256 scaledDeltaAmount0, int256 scaledDeltaAmount1)
    internal view returns (int256 deltaAmount0, int256 deltaAmount1)
{
    deltaAmount0 = SignedMath.ceilDiv(scaledDeltaAmount0, TOKEN_0_SCALE_MULTIPLIER);
    deltaAmount1 = SignedMath.ceilDiv(scaledDeltaAmount1, TOKEN_1_SCALE_MULTIPLIER);
}
``` [2](#0-1) 

`SignedMath.ceilDiv` rounds toward **positive infinity**. For a negative numerator and positive denominator, the exact result is negative, so "ceiling" means rounding toward zero — i.e., floor division on the magnitude:

```
ceilDiv(-1_500_000, 1_000_000) = -1   // not -2
``` [3](#0-2) 

The test suite confirms this: `ceilDiv(-7, 3) = -2`, `ceilDiv(-1, 2) = 0`. [4](#0-3) 

**Step 3 — The pool transfers only the rounded-down native amount.**

```solidity
if (amount1Delta < 0) {
    transferToken1(recipient, uint256(-amount1Delta));  // transfers 1, not 1.5
}
``` [5](#0-4) 

**Step 4 — The per-swap deficit.**

| Quantity | Value (example: `TOKEN_1_SCALE_MULTIPLIER = 10^6`) |
|---|---|
| `amountOutScaled` deducted from `binTotals.scaledToken1` | 1,500,000 |
| Native tokens transferred out | 1 |
| `balance1() * TOKEN_1_SCALE_MULTIPLIER` decrease | 1,000,000 |
| **Deficit per swap** | **500,000 scaled units** |

After N such swaps, `binTotals.scaledToken1` exceeds `balance1() * TOKEN_1_SCALE_MULTIPLIER` by up to `N * (TOKEN_1_SCALE_MULTIPLIER - 1)` scaled units.

**Step 5 — LP insolvency path.**

`LiquidityLib.removeLiquidity` computes each LP's entitlement from `binState.token1BalanceScaled` (which is consistent with `binTotals.scaledToken1`), then converts to native with `Math.Rounding.Floor` and calls `safeTransfer`: [6](#0-5) 

Because `binTotals.scaledToken1` (and the sum of all `token1BalanceScaled` across bins) exceeds the pool's actual `balance1() * TOKEN_1_SCALE_MULTIPLIER`, the last LP(s) to withdraw will find the pool has insufficient token1, causing their `safeTransfer` to revert. The `collectFees` function also performs an unchecked subtraction `balance1() * TOKEN_1_SCALE_MULTIPLIER - binTotals.scaledToken1` that will underflow once the deficit exceeds the initial surplus: [7](#0-6) 

---

### Impact Explanation

**Pool insolvency / direct LP principal loss.** Every exact-input swap where `amountOutScaled % TOKEN_1_SCALE_MULTIPLIER != 0` permanently widens the gap between `binTotals.scaledToken1` and `balance1() * TOKEN_1_SCALE_MULTIPLIER`. For a USDC pool (`TOKEN_1_SCALE_MULTIPLIER = 10^6`), the maximum deficit per swap is just under 1 USDC. Over thousands of swaps the deficit grows to material amounts, and the last LPs to withdraw cannot recover their full principal. `collectFees` also becomes permanently broken once the deficit exceeds the initial dust surplus.

---

### Likelihood Explanation

Any pool with `TOKEN_1_SCALE_MULTIPLIER > 1` (e.g., USDC/USDT as token1) is affected by every ordinary exact-input swap. No special attacker action is required — normal trading activity is sufficient. The condition `amountOutScaled % TOKEN_1_SCALE_MULTIPLIER != 0` is the common case for arbitrary swap sizes.

---

### Recommendation

Align the `binTotals.scaledToken1` deduction with the actual scaled amount that leaves the pool. After computing `amount1DeltaExternal` (the rounded native amount), derive the true scaled deduction as `uint256(-amount1DeltaExternal) * TOKEN_1_SCALE_MULTIPLIER` and use that value — not the raw `amountOutScaled` — when updating `binTotals.scaledToken1`. The rounding remainder should remain in the pool's accounting (and in the bin's `token1BalanceScaled`) as unclaimable dust, preserving the invariant `balance1() * TOKEN_1_SCALE_MULTIPLIER >= binTotals.scaledToken1`.

---

### Proof of Concept

```solidity
// TOKEN_1_SCALE_MULTIPLIER = 1_000_000 (USDC-like)
// Suppose one exact-input swap produces amountOutScaled = 1_500_000

// _executeSwap, line 739:
binTotals.scaledToken1 -= 1_500_000;   // full raw deduction

// deltasScaledToExternal:
// ceilDiv(-1_500_000, 1_000_000) = -1  (rounds toward zero)
amount1Delta = -1;

// swap(), line 254:
transferToken1(recipient, 1);           // only 1 USDC leaves

// Invariant check:
// balance1() * 1_000_000 decreased by 1_000_000
// binTotals.scaledToken1 decreased by 1_500_000
// => binTotals.scaledToken1 now exceeds balance1() * TOKEN_1_SCALE_MULTIPLIER by 500_000

// After 2001 such swaps (worst case ~1 USDC deficit each):
// binTotals.scaledToken1 > balance1() * TOKEN_1_SCALE_MULTIPLIER by > 1 USDC worth
// Last LP's removeLiquidity safeTransfer reverts.
// collectFees underflows at line 388.
```

Fuzz assertion to add after every swap:
```solidity
assert(balance1() * TOKEN_1_SCALE_MULTIPLIER >= uint256(binTotals.scaledToken1));
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L251-255)
```text
      if (amount1Delta < 0) {
        // casting to uint256 is safe because amount1Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken1(recipient, uint256(-amount1Delta));
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L387-388)
```text
    uint256 surplus1Scaled =
      balance1() * TOKEN_1_SCALE_MULTIPLIER - uint256(binTotals.scaledToken1) - notionalFee1AmountScaled;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L607-614)
```text
  function deltasScaledToExternal(int256 scaledDeltaAmount0, int256 scaledDeltaAmount1)
    internal
    view
    returns (int256 deltaAmount0, int256 deltaAmount1)
  {
    deltaAmount0 = SignedMath.ceilDiv(scaledDeltaAmount0, TOKEN_0_SCALE_MULTIPLIER);
    deltaAmount1 = SignedMath.ceilDiv(scaledDeltaAmount1, TOKEN_1_SCALE_MULTIPLIER);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L738-739)
```text
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken1 = uint128(uint256(binTotals.scaledToken1) - uint256(-amount1DeltaScaled));
```

**File:** metric-core/contracts/libraries/SignedMath.sol (L20-28)
```text
    int256 quotient = a / b;
    int256 remainder = a % b;

    // If there is a remainder and the exact result is positive, round up by 1.
    if (remainder != 0 && (a ^ b) >= 0) {
      unchecked {
        quotient += 1;
      }
    }
```

**File:** metric-core/test/SignedMath.t.sol (L31-34)
```text
    assertEq(SignedMath.ceilDiv(int256(-7), int256(3)), -2);
    assertEq(SignedMath.ceilDiv(int256(7), int256(-3)), -2);
    assertEq(SignedMath.ceilDiv(int256(-1), int256(2)), 0);
  }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L205-246)
```text
          uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
          uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;

          // casting to uint104 is safe because amount0Scaled and amount1Scaled are less than token(0|1)BalanceScaled
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token0BalanceScaled -= uint104(amount0Scaled);
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token1BalanceScaled -= uint104(amount1Scaled);
          binTotalShares[binIdx] = binTotalSharesVal - sharesToRemove;
          positionBinShares[posKey] = newUserShares;

          totalToken0ToRemoveScaled += amount0Scaled;
          totalToken1ToRemoveScaled += amount1Scaled;

          binBalanceDeltas[i] = BinBalanceDelta({
            // safe because amount0Scaled is bounded by uint104 bin balances.
            // forge-lint: disable-next-line(unsafe-typecast)
            delta0Scaled: -int256(amount0Scaled),
            // safe because amount1Scaled is bounded by uint104 bin balances.
            // forge-lint: disable-next-line(unsafe-typecast)
            delta1Scaled: -int256(amount1Scaled)
          });
        }
      }

      if (totalToken0ToRemoveScaled > 0) {
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken0 = uint128(uint256(binTotals.scaledToken0) - totalToken0ToRemoveScaled);
      }
      if (totalToken1ToRemoveScaled > 0) {
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken1 = uint128(uint256(binTotals.scaledToken1) - totalToken1ToRemoveScaled);
      }

      (amount0Removed, amount1Removed) =
        _deltasScaledToExternal(totalToken0ToRemoveScaled, totalToken1ToRemoveScaled, ctx, Math.Rounding.Floor);

      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
      if (amount1Removed > 0) {
        IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
```
