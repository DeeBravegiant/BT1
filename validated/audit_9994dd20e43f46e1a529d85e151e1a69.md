### Title
LP Shares Burned With Zero Token Return in `removeLiquidity` Due to Floor Division — (`metric-core/contracts/libraries/LiquidityLib.sol`)

### Summary

`LiquidityLib.removeLiquidity` uses floor (integer) division to compute the token amounts owed to an LP when they burn shares. When the bin's scaled token balance is small relative to the total shares outstanding, the division rounds to zero. The shares are still permanently burned and the position is updated, but the LP receives no tokens in return — a direct loss of LP principal with no revert guard.

### Finding Description

In `LiquidityLib.removeLiquidity`, the per-bin token amounts are computed at lines 205–206:

```solidity
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
```

Both divisions are plain integer (floor) division. If `binState.token0BalanceScaled * sharesToRemove < binTotalSharesVal`, then `amount0Scaled = 0`. The code then unconditionally burns the shares and updates state:

```solidity
binState.token0BalanceScaled -= uint104(amount0Scaled);   // subtracts 0
binTotalShares[binIdx] = binTotalSharesVal - sharesToRemove; // shares destroyed
positionBinShares[posKey] = newUserShares;                  // position reduced
```

A second floor division occurs in `_deltasScaledToExternal` (line 275–276):

```solidity
deltaAmount0 = scaledDeltaAmount0 / ctx.token0ScaleMultiplier;
```

For tokens with fewer than 18 decimals (e.g. USDC, where `token0ScaleMultiplier = 10^12`), even a non-zero `amount0Scaled` can round to zero here. Tokens are only transferred when the final amount is positive (lines 242–246), so the LP receives nothing.

The existing `minimalMintableLiquidity` guard at line 200 only checks that the *remaining* position after removal is above the minimum — it does not check whether the *removed* shares produce any non-zero token output. When a user removes all their shares (`newUserShares == 0`), even this guard is bypassed entirely.

By contrast, `addLiquidity` uses `Math.ceilDiv` throughout (lines 87–110), ensuring the pool always receives at least 1 scaled unit per share added. No symmetric floor-rounding guard exists on the withdrawal path.

### Impact Explanation

An LP who calls `removeLiquidity` on a bin whose token balance has been depleted by swaps (leaving `token0BalanceScaled` small relative to `binTotalSharesVal`) will have their shares permanently burned while receiving zero tokens. This is a direct, unrecoverable loss of LP principal. The burned shares also reduce `binTotalShares`, concentrating the remaining balance among other LPs — effectively transferring value from the withdrawing LP to remaining LPs.

### Likelihood Explanation

Bins naturally accumulate this condition during normal operation: as swaps consume token0 from a bin, `token0BalanceScaled` decreases while `binTotalSharesVal` remains unchanged. Any LP who then removes a small number of shares (or removes shares from a nearly-exhausted bin) triggers the zero-return path. No attacker is required; this is a self-harm scenario for any LP interacting with a depleted bin. The `minimalMintableLiquidity` parameter does not mitigate this because it governs position size, not token output per share.

### Recommendation

Add a guard in `removeLiquidity` that reverts when the computed token amounts are both zero but the user is burning non-zero shares:

```solidity
if (amount0Scaled == 0 && amount1Scaled == 0 && sharesToRemove > 0) {
    revert ZeroTokensForShares(sharesToRemove);
}
```

Alternatively, apply the same check after `_deltasScaledToExternal` at the aggregate level. This mirrors the fix recommended for the analogous Popcorn M-33 finding: revert rather than silently burn shares for zero output.

### Proof of Concept

**Setup:**
- Pool with token0 (18 decimals, `token0ScaleMultiplier = 1`) and token1 (18 decimals).
- Bin above current price: initially `token0BalanceScaled = 1000`, `binTotalSharesVal = 1_000_000`.
- After many swaps consume token0, `token0BalanceScaled` drops to `1`.

**Attack / Loss Scenario:**
1. Alice holds `sharesToRemove = 500` shares in this bin (`binTotalSharesVal = 1_000_000`).
2. Alice calls `removeLiquidity` with `sharesToRemove = 500`.
3. `amount0Scaled = (1 * 500) / 1_000_000 = 0` (floor division).
4. `amount1Scaled = 0` (bin above current price has no token1).
5. `binTotalShares[binIdx]` is decremented by 500; `positionBinShares[posKey]` is decremented by 500.
6. `amount0Removed = 0 / 1 = 0`; no `safeTransfer` is called.
7. Alice's 500 shares are permanently burned; she receives 0 tokens.

The same scenario applies to a USDC pool where `token0ScaleMultiplier = 10^12`: even `amount0Scaled = 1` (one internal unit) produces `amount0Removed = 1 / 10^12 = 0` external USDC, while the shares are still burned. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L108-110)
```text
          } else {
            amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
            amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L196-202)
```text
          if (userShares < sharesToRemove) {
            revert IMetricOmmPoolActions.InsufficientLiquidity(sharesToRemove, userShares);
          }
          uint256 newUserShares = userShares - sharesToRemove;
          if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L204-214)
```text
          BinState storage binState = binStates[binIdx];
          uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
          uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;

          // casting to uint104 is safe because amount0Scaled and amount1Scaled are less than token(0|1)BalanceScaled
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token0BalanceScaled -= uint104(amount0Scaled);
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token1BalanceScaled -= uint104(amount1Scaled);
          binTotalShares[binIdx] = binTotalSharesVal - sharesToRemove;
          positionBinShares[posKey] = newUserShares;
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L239-246)
```text
      (amount0Removed, amount1Removed) =
        _deltasScaledToExternal(totalToken0ToRemoveScaled, totalToken1ToRemoveScaled, ctx, Math.Rounding.Floor);

      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
      if (amount1Removed > 0) {
        IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L265-278)
```text
  function _deltasScaledToExternal(
    uint256 scaledDeltaAmount0,
    uint256 scaledDeltaAmount1,
    PoolContext memory ctx,
    Math.Rounding rounding
  ) internal pure returns (uint256 deltaAmount0, uint256 deltaAmount1) {
    if (rounding == Math.Rounding.Ceil) {
      deltaAmount0 = Math.ceilDiv(scaledDeltaAmount0, ctx.token0ScaleMultiplier);
      deltaAmount1 = Math.ceilDiv(scaledDeltaAmount1, ctx.token1ScaleMultiplier);
    } else {
      deltaAmount0 = scaledDeltaAmount0 / ctx.token0ScaleMultiplier;
      deltaAmount1 = scaledDeltaAmount1 / ctx.token1ScaleMultiplier;
    }
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L48-51)
```text
  /// @notice Multiplier to scale token0 external amounts to internal: 10^(max(18, decimals) - token0.decimals())
  uint256 internal immutable TOKEN_0_SCALE_MULTIPLIER;
  /// @notice Multiplier to scale token1 external amounts to internal: 10^(max(18, decimals) - token1.decimals())
  uint256 internal immutable TOKEN_1_SCALE_MULTIPLIER;
```
