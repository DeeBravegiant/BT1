### Title
LP shares burned with zero token return in `removeLiquidity` due to unchecked floor rounding — (`metric-core/contracts/libraries/LiquidityLib.sol`)

---

### Summary

`LiquidityLib.removeLiquidity` burns an LP's shares and updates all accounting state, but does not revert when the computed token amounts round to zero. The LP loses their shares and receives nothing; the transaction succeeds silently.

---

### Finding Description

`removeLiquidity` computes the scaled token amounts owed to the LP via integer floor division at two successive levels:

**Level 1 — share-to-scaled:** [1](#0-0) 

```solidity
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
```

When `binState.token0BalanceScaled * sharesToRemove < binTotalSharesVal`, `amount0Scaled` truncates to 0.

**Level 2 — scaled-to-native (Floor):** [2](#0-1) [3](#0-2) 

```solidity
deltaAmount0 = scaledDeltaAmount0 / ctx.token0ScaleMultiplier;
```

For a low-decimal token such as USDC (6 decimals), `token0ScaleMultiplier = 10^12`. Any `totalToken0ToRemoveScaled < 10^12` produces `amount0Removed = 0`.

Despite both amounts being zero, the function still executes all state mutations: [4](#0-3) 

```solidity
binState.token0BalanceScaled -= uint104(amount0Scaled);   // may be no-op if amount0Scaled==0
binState.token1BalanceScaled -= uint104(amount1Scaled);
binTotalShares[binIdx] = binTotalSharesVal - sharesToRemove;  // shares always burned
positionBinShares[posKey] = newUserShares;                    // position always updated
```

Then the transfer block is silently skipped: [5](#0-4) 

```solidity
if (amount0Removed > 0) { IERC20(ctx.token0).safeTransfer(owner, amount0Removed); }
if (amount1Removed > 0) { IERC20(ctx.token1).safeTransfer(owner, amount1Removed); }
```

No revert is issued. The LP's shares are permanently destroyed with zero compensation.

The only existing guard is the `MinimalLiquidity` check on the *remaining* shares: [6](#0-5) 

```solidity
if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
}
```

This guard does not protect against the case where the *removed* shares yield zero tokens.

**Two independent rounding paths to the same outcome:**

| Path | Condition | Effect |
|---|---|---|
| Share-to-scaled rounds to 0 | `token0BalanceScaled * sharesToRemove < binTotalSharesVal` | Bin balance unchanged; remaining LPs gain proportional claim |
| Scaled-to-native rounds to 0 | `totalToken0ToRemoveScaled < token0ScaleMultiplier` | Bin balance decreases but no tokens leave; surplus accrues to fee collector via `collectFees` |

In the second path, `binTotals.scaledToken0` is decremented while the actual ERC-20 balance is unchanged, creating an untracked surplus that is swept as protocol/admin spread fees in `collectFees`: [7](#0-6) 

---

### Impact Explanation

An LP burns shares and receives zero tokens. In the share-to-scaled path, the burned value is redistributed to remaining LPs. In the scaled-to-native path, the burned value becomes untracked surplus collected by the fee receiver. Either way, the LP suffers a direct, unrecoverable loss of principal with no protocol-level revert to protect them. This breaks the core solvency invariant that pool balances must always cover all LP claims.

---

### Likelihood Explanation

- **USDC/USDT pools** (6-decimal tokens, `token0ScaleMultiplier = 10^12`): any removal whose proportional scaled claim is below `10^12` (i.e., worth less than 1 native unit) silently yields zero. This is reachable with small positions or high total-share bins.
- **High total-share bins**: after many LPs deposit, a small LP's `sharesToRemove * token0BalanceScaled < binTotalSharesVal` can hold even for the minimum mintable share count.
- The LP is the transaction initiator, so no external attacker is required; the loss is self-inflicted but unprotected by the protocol.

---

### Recommendation

Add a guard in `removeLiquidity` that reverts when shares are non-zero but both computed native amounts are zero, mirroring the fix applied to the AToken `redeem` bug:

```solidity
// After computing amount0Removed and amount1Removed:
if (sharesToRemove > 0 && amount0Removed == 0 && amount1Removed == 0) {
    revert ZeroTokensForShares();
}
```

Alternatively, enforce a minimum per-bin removal that guarantees at least 1 native unit of at least one token is returned, analogous to the `MinimalLiquidity` guard on the add path.

---

### Proof of Concept

**Setup**: USDC (6 decimals) / WETH (18 decimals) pool. `token0ScaleMultiplier = 10^12`. `MINIMAL_MINTABLE_LIQUIDITY = 1000`.

1. LP adds `1000` shares to bin `+1` (above active bin, token0-only). Initial scaled balance: `token0BalanceScaled = 1` (if `initialScaledToken0PerShareE18 = 1`). `binTotalShares[1] = 1000`.

2. LP calls `removeLiquidity` with `sharesToRemove = 1000` (full exit, `newUserShares = 0`, passes `MinimalLiquidity` check).

3. `amount0Scaled = (1 * 1000) / 1000 = 1`. Non-zero, passes Level 1.

4. `amount0Removed = 1 / 10^12 = 0`. Rounds to zero at Level 2.

5. `binState.token0BalanceScaled -= 1` (decremented), `binTotals.scaledToken0 -= 1` (decremented), `positionBinShares[posKey] = 0` (shares burned).

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L239-240)
```text
      (amount0Removed, amount1Removed) =
        _deltasScaledToExternal(totalToken0ToRemoveScaled, totalToken1ToRemoveScaled, ctx, Math.Rounding.Floor);
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

**File:** metric-core/contracts/MetricOmmPool.sol (L385-388)
```text
    uint256 surplus0Scaled =
      balance0() * TOKEN_0_SCALE_MULTIPLIER - uint256(binTotals.scaledToken0) - notionalFee0AmountScaled;
    uint256 surplus1Scaled =
      balance1() * TOKEN_1_SCALE_MULTIPLIER - uint256(binTotals.scaledToken1) - notionalFee1AmountScaled;
```
