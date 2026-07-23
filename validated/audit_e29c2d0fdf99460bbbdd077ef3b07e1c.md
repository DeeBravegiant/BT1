Audit Report

## Title
LP shares permanently burned with zero token return in `removeLiquidity` due to unchecked floor rounding — (`metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary

`LiquidityLib.removeLiquidity` burns an LP's shares and updates all accounting state but never reverts when both computed token amounts round to zero via integer floor division. The LP's shares are permanently destroyed and they receive no tokens in return. The transaction succeeds silently with no protocol-level protection.

## Finding Description

Two independent floor-rounding truncations can each independently produce a zero token return:

**Level 1 — share-to-scaled (floor division):** [1](#0-0) 

When `binState.token0BalanceScaled * sharesToRemove < binTotalSharesVal`, `amount0Scaled` truncates to 0. The bin balance is not decremented, but shares are still burned — the burned value is silently redistributed to remaining LPs.

**Level 2 — scaled-to-native (floor division):** [2](#0-1) 

For USDC (6 decimals), `token0ScaleMultiplier = 10^12`. Any `totalToken0ToRemoveScaled < 10^12` produces `amount0Removed = 0`. The bin balance and `binTotals.scaledToken0` are decremented, but no ERC-20 transfer occurs — the discrepancy becomes untracked surplus swept as protocol/admin spread fees via `collectFees`. [3](#0-2) 

Despite both amounts being zero, all state mutations execute unconditionally: [4](#0-3) 

The transfer block is then silently skipped: [5](#0-4) 

The only existing guard checks the *remaining* shares, not whether the *removed* shares yield any tokens: [6](#0-5) 

This guard is entirely insufficient to protect against the zero-return case.

## Impact Explanation

An LP burns shares and receives zero tokens — a direct, unrecoverable loss of principal. In the share-to-scaled path, the burned value is redistributed to remaining LPs. In the scaled-to-native path, the burned value becomes untracked surplus collected by the fee receiver. Either way, the LP suffers a complete loss of the removed position's value. This breaks the core solvency invariant that pool balances must always cover all LP claims, and constitutes a direct loss of user principal above Sherlock thresholds when the position has non-trivial value in scaled units.

## Likelihood Explanation

- **USDC/USDT pools** (6-decimal tokens, `token0ScaleMultiplier = 10^12`): any removal whose proportional scaled claim is below `10^12` silently yields zero. This is reachable with small positions or high total-share bins.
- **High total-share bins**: after many LPs deposit, a small LP's `sharesToRemove * token0BalanceScaled < binTotalSharesVal` can hold even for the minimum mintable share count.
- The LP is the transaction initiator — no external attacker is required. The loss is self-inflicted but entirely unprotected by the protocol.
- The condition is repeatable and deterministic; any LP with a sufficiently small proportional claim in a USDC/USDT pool is at risk.

## Recommendation

Add a guard in `removeLiquidity` after computing `amount0Removed` and `amount1Removed` that reverts when shares are non-zero but both computed native amounts are zero:

```solidity
if (sharesToRemove > 0 && amount0Removed == 0 && amount1Removed == 0) {
    revert ZeroTokensForShares();
}
```

Alternatively, enforce a minimum per-bin removal that guarantees at least 1 native unit of at least one token is returned, analogous to the `MinimalLiquidity` guard applied on the add path. [7](#0-6) 

## Proof of Concept

**Setup**: USDC (6 decimals) / WETH (18 decimals) pool. `token0ScaleMultiplier = 10^12`. `MINIMAL_MINTABLE_LIQUIDITY = 1000`.

1. LP adds `1000` shares to bin `+1` (above active bin, token0-only). `token0BalanceScaled = 1`, `binTotalShares[1] = 1000`.
2. LP calls `removeLiquidity` with `sharesToRemove = 1000` (full exit, `newUserShares = 0`, passes `MinimalLiquidity` check).
3. `amount0Scaled = (1 * 1000) / 1000 = 1`. Non-zero — passes Level 1.
4. `totalToken0ToRemoveScaled = 1`. `amount0Removed = 1 / 10^12 = 0`. Rounds to zero at Level 2.
5. `binState.token0BalanceScaled -= 1`, `binTotals.scaledToken0 -= 1`, `positionBinShares[posKey] = 0` — shares burned.
6. No `safeTransfer` executes. LP receives 0 USDC.
7. The 1 scaled unit of token0 is now untracked surplus, collectible as fees via `collectFees`.

The LP's shares are permanently destroyed; they receive nothing.

### Citations

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L200-202)
```text
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L239-247)
```text
      (amount0Removed, amount1Removed) =
        _deltasScaledToExternal(totalToken0ToRemoveScaled, totalToken1ToRemoveScaled, ctx, Math.Rounding.Floor);

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

**File:** metric-core/contracts/MetricOmmPool.sol (L385-388)
```text
    uint256 surplus0Scaled =
      balance0() * TOKEN_0_SCALE_MULTIPLIER - uint256(binTotals.scaledToken0) - notionalFee0AmountScaled;
    uint256 surplus1Scaled =
      balance1() * TOKEN_1_SCALE_MULTIPLIER - uint256(binTotals.scaledToken1) - notionalFee1AmountScaled;
```
