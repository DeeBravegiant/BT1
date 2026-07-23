### Title
LP Shares Burned for Zero Token Payout via Floor Division in `removeLiquidity` — (`metric-core/contracts/libraries/LiquidityLib.sol`)

---

### Summary

`LiquidityLib.removeLiquidity` uses integer floor division to compute each token's proportional payout. When a bin's scaled balance is small relative to total shares, both `amount0Scaled` and `amount1Scaled` can round to zero. The function then burns the LP's shares and decrements `binTotalShares` without transferring any tokens, permanently destroying LP principal.

---

### Finding Description

In `LiquidityLib.removeLiquidity`, the per-bin payout is computed as:

```solidity
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
``` [1](#0-0) 

Both divisions floor to zero when `binState.token0BalanceScaled * sharesToRemove < binTotalSharesVal`. The function then unconditionally decrements shares and total shares:

```solidity
binState.token0BalanceScaled -= uint104(amount0Scaled);   // subtracts 0
binTotalShares[binIdx] = binTotalSharesVal - sharesToRemove;
positionBinShares[posKey] = newUserShares;
``` [2](#0-1) 

Token transfers are guarded by `if (amount0Removed > 0)` / `if (amount1Removed > 0)`, so when both round to zero, no transfer occurs and the function returns `(0, 0)` silently: [3](#0-2) 

There is no guard of the form `require(amount0Scaled > 0 || amount1Scaled > 0)` before the share decrement. The `minimalMintableLiquidity` check only prevents leaving a dust remainder — it does not prevent burning all shares for zero tokens: [4](#0-3) 

A second floor rounding in `_deltasScaledToExternal` (`scaledDeltaAmount0 / ctx.token0ScaleMultiplier`) can also produce zero native transfer even when `amount0Scaled > 0` but is below the scale multiplier (e.g., `token0ScaleMultiplier = 1e12` for a 6-decimal token): [5](#0-4) 

**Note on the U64x32 attribution:** The proposed mechanism — that U64x32 oracle precision loss causes swaps to drain the bin to near-zero — is not the actual mechanism. U64x32 rounding error is at most 1 LSB of a 27-bit mantissa, which is negligible. The bin balance reaches near-zero through ordinary directional swaps consuming all of one token side in a bin. The vulnerability exists independently of oracle precision.

---

### Impact Explanation

An LP with a valid, non-zero share position calls `removeLiquidity` on a bin whose scaled balance has been reduced to a small value by normal swap activity. The floor division produces `amount0Scaled = 0` and `amount1Scaled = 0`. The LP's shares are permanently burned, `binTotalShares` is decremented, but the LP receives zero tokens. This is direct LP principal loss with no recovery path.

The remaining bin balance (the dust that was not paid out) is now owned by the remaining LPs, so the attacker can repeat the pattern to extract that dust by being the last LP — or simply grief honest LPs.

---

### Likelihood Explanation

The condition is reachable through normal pool operation:

1. A bin above the current price holds only token0. Swaps buying token0 drain it.
2. After many swaps, `token0BalanceScaled` can be reduced to a small value (e.g., 1–100 scaled units).
3. Any LP whose `sharesToRemove * token0BalanceScaled < binTotalSharesVal` will receive zero.
4. The `minimalMintableLiquidity` floor (e.g., 1000 shares in tests) means the minimum position is 1000 shares, but `binTotalSharesVal` can be much larger (e.g., 1,000,000), making the condition `1 * 1000 / 1,000,000 = 0` trivially reachable. [6](#0-5) 

---

### Recommendation

Add a guard before burning shares that reverts (or skips) when both computed payouts are zero but the bin has non-zero balance:

```solidity
if (amount0Scaled == 0 && amount1Scaled == 0 &&
    (binState.token0BalanceScaled > 0 || binState.token1BalanceScaled > 0)) {
    revert ZeroPayoutForNonZeroBalance();
}
```

Alternatively, use `Math.mulDiv` with `Rounding.Ceil` for the payout (matching the `addLiquidity` direction) so the LP always receives at least 1 scaled unit when the bin has any balance.

---

### Proof of Concept

```solidity
// Setup: bin above current price, token0 only
// LP A: 999_000 shares, LP B: 1_000 shares (minimalMintableLiquidity)
// binTotalShares = 1_000_000
// After swaps: token0BalanceScaled = 500 (drained by normal swaps)

// LP B calls removeLiquidity with all 1_000 shares:
// amount0Scaled = 500 * 1_000 / 1_000_000 = 0  (floor)
// amount1Scaled = 0 (bin is one-sided)
// LP B's 1_000 shares are burned, receives 0 token0
// binTotalShares becomes 999_000
// token0BalanceScaled remains 500 (owned entirely by LP A now)
```

### Citations

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L77-79)
```text
          if (newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L199-202)
```text
          uint256 newUserShares = userShares - sharesToRemove;
          if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L205-206)
```text
          uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
          uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L210-214)
```text
          binState.token0BalanceScaled -= uint104(amount0Scaled);
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token1BalanceScaled -= uint104(amount1Scaled);
          binTotalShares[binIdx] = binTotalSharesVal - sharesToRemove;
          positionBinShares[posKey] = newUserShares;
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L242-247)
```text
      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
      if (amount1Removed > 0) {
        IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
      }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L274-276)
```text
    } else {
      deltaAmount0 = scaledDeltaAmount0 / ctx.token0ScaleMultiplier;
      deltaAmount1 = scaledDeltaAmount1 / ctx.token1ScaleMultiplier;
```
