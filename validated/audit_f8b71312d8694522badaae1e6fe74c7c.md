Audit Report

## Title
Free Share Minting in Fully-Drained Bins Dilutes Existing LP Claims - (File: metric-core/contracts/libraries/LiquidityLib.sol)

## Summary
When a swap fully drains a bin's token balances to zero while `binTotalShares` remains non-zero, any caller can invoke `addLiquidity` to mint an arbitrary number of shares in that bin without depositing any tokens. The proportional calculation yields zero for both token amounts, the settlement callback is skipped, yet shares are unconditionally credited. When a reverse swap later refills the bin, the attacker's free shares entitle them to a proportional claim on the new tokens, directly stealing LP principal.

## Finding Description

In `LiquidityLib.addLiquidity`, when `binTotalSharesVal > 0`, required token amounts are computed proportionally:

```solidity
amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
``` [1](#0-0) 

If a swap has fully drained the bin (`token0BalanceScaled == 0` and `token1BalanceScaled == 0`), both `_checkedMul(0, sharesToAdd)` return `0`, and `Math.ceilDiv(0, binTotalSharesVal)` returns `0`. Consequently, `totalToken0ToAddScaled` and `totalToken1ToAddScaled` remain zero, `amount0Added` and `amount1Added` are both zero, and the settlement callback is never invoked:

```solidity
if (amount0Added > 0 || amount1Added > 0) {
    // callback NOT invoked — no tokens pulled from caller
}
``` [2](#0-1) 

However, shares are **unconditionally** minted regardless of whether any tokens were deposited:

```solidity
binTotalShares[binIdx] = binTotalSharesVal + sharesToAdd;
positionBinShares[posKey] = newUserShares;
``` [3](#0-2) 

The only existing guard is the `minimalMintableLiquidity` dust floor check, which verifies that `newUserShares >= minimalMintableLiquidity` — it does not require any token deposit:

```solidity
if (newUserShares < ctx.minimalMintableLiquidity) {
    revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
}
``` [4](#0-3) 

Swaps do not modify `_binTotalShares` — only `addLiquidity`/`removeLiquidity` do — so after a draining swap, `binTotalShares[binIdx]` remains at its pre-swap value while `token0BalanceScaled` and/or `token1BalanceScaled` are zero. This is a reachable state in normal pool operation: any bin above the current price holds only token0, and a sufficiently large `zeroForOne` swap can consume all of it. [5](#0-4) 

## Impact Explanation

When a reverse swap later refills the drained bin, the attacker's free shares entitle them to a proportional claim on the incoming tokens. Existing LPs receive fewer tokens than they are owed — a direct loss of LP principal. The attacker can scale the attack by minting an arbitrarily large number of shares (e.g., minting 1,000,000 shares against 10,000 existing shares captures ~99% of future refill tokens). This constitutes pool insolvency from the LP's perspective: the pool's token balance is correct, but the share registry no longer accurately represents LP entitlements. This meets the Critical/High threshold for direct loss of user principal and pool insolvency under the allowed impact gate.

## Likelihood Explanation

Any swap that fully drains a bin creates this window. Large swaps or swaps in low-liquidity bins routinely drain bins completely during normal operation. The attacker monitors the chain for `BinSwapped` events showing a bin balance reaching zero, then calls `addLiquidity` in the next block. No special permissions or privileged access are required — `addLiquidity` is fully permissionless and callable by any address. [6](#0-5) 

## Recommendation

In `LiquidityLib.addLiquidity`, inside the `binTotalSharesVal > 0` branch, add a guard that reverts when both token balances are zero:

```solidity
} else {
    if (binState.token0BalanceScaled == 0 && binState.token1BalanceScaled == 0) {
        revert DrainedBin(binIdx);
    }
    amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
    amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
}
```

Alternatively, fall back to the initial per-share rate path (the same path used when `binTotalSharesVal == 0`) so that tokens proportional to `initialScaledToken0PerShareE18` / `initialScaledToken1PerShareE18` are required, preserving the ability to add liquidity to a drained bin while ensuring tokens are deposited. [7](#0-6) 

## Proof of Concept

1. **LP1** calls `addLiquidity` for bin `X` (above current price, token0-only): `token0BalanceScaled = 1000`, `binTotalShares[X] = 10000`.
2. A large `zeroForOne` swap fully consumes bin `X`: `token0BalanceScaled = 0`, `binTotalShares[X] = 10000` (unchanged by swap).
3. **Attacker** calls `addLiquidity` for bin `X` with `sharesToAdd = 10000`:
   - `amount0Scaled = Math.ceilDiv(0 * 10000 / 10000) = 0`
   - `amount1Scaled = 0`
   - `amount0Added = 0`, `amount1Added = 0` → callback skipped, zero tokens paid
   - `binTotalShares[X] = 20000`, `positionBinShares[attacker][X] = 10000`
4. A reverse swap refills bin `X` with 1000 token0 (updating `token0BalanceScaled = 1000`).
5. **LP1** calls `removeLiquidity`: receives `10000 * 1000 / 20000 = 500` token0 (should be 1000).
6. **Attacker** calls `removeLiquidity`: receives `10000 * 1000 / 20000 = 500` token0 at zero cost.

LP1 loses 500 token0 of principal. The attacker can amplify by minting far more shares than the existing total (e.g., `sharesToAdd = 1_000_000` captures ~99% of future refill tokens). [8](#0-7)

### Citations

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L77-79)
```text
          if (newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L85-121)
```text
          if (binTotalSharesVal == 0) {
            if (binIdx < curBinIdxCache) {
              amount1Scaled = Math.ceilDiv(_checkedMul(ctx.initialScaledToken1PerShareE18, sharesToAdd), 1e18);
            } else if (binIdx > curBinIdxCache) {
              amount0Scaled = Math.ceilDiv(_checkedMul(ctx.initialScaledToken0PerShareE18, sharesToAdd), 1e18);
            } else {
              uint256 token0Proportion = type(uint104).max - ctx.curPosInBin;
              uint256 token1Proportion = ctx.curPosInBin;
              amount0Scaled =
              (Math.mulDiv(
                  token0Proportion * ctx.initialScaledToken0PerShareE18,
                  sharesToAdd,
                  uint256(type(uint104).max) * 1e18,
                  Math.Rounding.Ceil
                ));
              amount1Scaled =
              (Math.mulDiv(
                  token1Proportion * ctx.initialScaledToken1PerShareE18,
                  sharesToAdd,
                  uint256(type(uint104).max) * 1e18,
                  Math.Rounding.Ceil
                ));
            }
          } else {
            amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
            amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
          }
          if (amount0Scaled > 0) {
            totalToken0ToAddScaled += amount0Scaled;
            binState.token0BalanceScaled = (uint256(binState.token0BalanceScaled) + amount0Scaled).toUint104();
          }
          if (amount1Scaled > 0) {
            totalToken1ToAddScaled += amount1Scaled;
            binState.token1BalanceScaled = (uint256(binState.token1BalanceScaled) + amount1Scaled).toUint104();
          }
          binTotalShares[binIdx] = binTotalSharesVal + sharesToAdd;
          positionBinShares[posKey] = newUserShares;
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L144-155)
```text
      if (amount0Added > 0 || amount1Added > 0) {
        uint256 balance0Before = IERC20(ctx.token0).balanceOf(address(this));
        uint256 balance1Before = IERC20(ctx.token1).balanceOf(address(this));
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
        if (amount0Added > 0 && balance0Before + amount0Added > IERC20(ctx.token0).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
        if (amount1Added > 0 && balance1Before + amount1Added > IERC20(ctx.token1).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```
