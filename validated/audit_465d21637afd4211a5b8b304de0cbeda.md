Audit Report

## Title
LP Share Burn With Zero Token Return Due to Rounding in `removeLiquidity` — (`metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary

`LiquidityLib.removeLiquidity` computes per-bin token amounts owed to a withdrawing LP using integer floor division. When `binState.token0BalanceScaled * sharesToRemove < binTotalSharesVal`, both `amount0Scaled` and `amount1Scaled` round to zero. The function unconditionally burns the LP's shares and clears their position, while transferring nothing. The forfeited bin balance is silently redistributed to remaining LPs.

## Finding Description

At lines 205–206, the token amounts owed are computed with plain floor division:

```solidity
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
``` [1](#0-0) 

When both results are zero, the code at lines 210–214 still unconditionally updates state — subtracting zero from bin balances, burning the user's shares, and clearing their position:

```solidity
binState.token0BalanceScaled -= uint104(amount0Scaled);
binState.token1BalanceScaled -= uint104(amount1Scaled);
binTotalShares[binIdx] = binTotalSharesVal - sharesToRemove;
positionBinShares[posKey] = newUserShares;
``` [2](#0-1) 

There is no guard that reverts or skips the state update when both computed amounts are zero. The subsequent transfer at lines 242–246 is conditional on non-zero amounts, so no tokens are sent: [3](#0-2) 

A second rounding point exists in `_deltasScaledToExternal` at lines 274–276: even if `amount0Scaled > 0`, dividing by `token0ScaleMultiplier` (up to `1e12` for a 6-decimal token) can produce zero external units, again with no revert: [4](#0-3) 

This is asymmetric with `addLiquidity`, which uses `Math.ceilDiv` at lines 109–110 to charge the LP on deposit — rounding always favors the pool: [5](#0-4) 

The existing `minimalMintableLiquidity` guard at lines 200–202 only prevents leaving a dust *remainder* position; it does not prevent a full removal (`newUserShares == 0`) that yields zero tokens: [6](#0-5) 

## Impact Explanation

An LP who calls `removeLiquidity` under the triggering conditions permanently loses their entire position in the affected bin. Their shares are burned, the bin balance is not reduced, and no tokens are transferred. This is a direct, irreversible loss of user principal — a broken LP withdraw flow causing loss of LP assets — matching the allowed-impact gate. The forfeited balance increases the proportional claim of remaining LPs.

## Likelihood Explanation

The triggering condition `binState.token0BalanceScaled * sharesToRemove < binTotalSharesVal` is reachable in normal operation. Swaps drain bin balances without affecting LP share counts, so a heavily-traded bin can reach a state where its scaled balance is a few units while total shares remain large. A small LP holding the minimum `minimalMintableLiquidity` shares removing their full position in such a bin will trigger the rounding. The user can trigger this unknowingly via a router that does not pre-simulate output, and there is no on-chain protection. Because `removeLiquidity` requires `msg.sender == owner`, the loss cannot be forced on a victim by a third party, but it is self-inflicted with no warning.

## Recommendation

Add a revert before burning shares when both computed amounts are zero:

```solidity
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;

if (amount0Scaled == 0 && amount1Scaled == 0) {
    revert IMetricOmmPoolActions.ZeroTokensOut();
}
```

Alternatively, use `Math.ceilDiv` for the LP's share of the bin balance (rounding in the LP's favour, consistent with how `addLiquidity` rounds against the LP):

```solidity
uint256 amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToRemove), binTotalSharesVal);
uint256 amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToRemove), binTotalSharesVal);
```

## Proof of Concept

**Setup:**
- Pool with a 6-decimal token (USDC) as token0; `token0ScaleMultiplier = 1e12`.
- Bin 0 has been traded down to `token0BalanceScaled = 500`.
- `binTotalShares[0] = 1_000_000e18` (many LPs, each holding `minimalMintableLiquidity = 1e18` shares).
- Alice holds `1e18` shares in bin 0.

**Steps:**
1. Alice calls `removeLiquidity` with `sharesToRemove = 1e18` (her full position).
2. `amount0Scaled = (500 * 1e18) / (1_000_000e18) = 0` (floor division).
3. `amount1Scaled = 0` (bin has no token1).
4. `binState.token0BalanceScaled -= 0` → unchanged at 500.
5. `binTotalShares[0] = 999_999e18` — Alice's shares burned.
6. `positionBinShares[aliceKey] = 0` — Alice's position erased.
7. `amount0Removed = 0 / 1e12 = 0`; no transfer.
8. Alice receives nothing. The 500 scaled units remain in the bin, now owned by the remaining 999,999 LPs.

### Citations

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L109-110)
```text
            amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
            amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L242-246)
```text
      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
      if (amount1Removed > 0) {
        IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L274-276)
```text
    } else {
      deltaAmount0 = scaledDeltaAmount0 / ctx.token0ScaleMultiplier;
      deltaAmount1 = scaledDeltaAmount1 / ctx.token1ScaleMultiplier;
```
