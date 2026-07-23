### Title
LP Fee Front-Running via Sandwich Attack on Bin Share Price - (`metric-core/contracts/libraries/LiquidityLib.sol`)

### Summary

Every swap deposits the LP-fee portion of the input token directly into the touched bin's `token0BalanceScaled` / `token1BalanceScaled`. Because `removeLiquidity` redeems shares at the current per-share bin balance with no exit fee or lock period, an attacker can sandwich any pending swap: add shares just before the swap, let the swap inflate the bin balance with LP fees, then immediately remove shares and pocket a proportional slice of those fees. This is the direct analog of the Union Finance `exchangeRateStored()` front-running bug.

### Finding Description

**How LP fees accumulate in bins**

In `SwapMath.buyToken0InBinSpecifiedOut` the LP fee is computed and left inside the bin:

```solidity
uint256 feeAmountScaled = Math.ceilDiv(amountInScaled * currBinBuyFeeX64, ONE_X64);
amountInScaled += feeAmountScaled;
uint256 protocolFeeAmountScaled = (feeAmountScaled * spreadFeeE6) / 1e6;

binState.token0BalanceScaled -= amountOutScaled.toUint104();
binState.token1BalanceScaled =
  (uint256(binState.token1BalanceScaled) + amountInScaled - protocolFeeAmountScaled).toUint104();
``` [1](#0-0) 

The LP fee (`feeAmountScaled - protocolFeeAmountScaled`) is permanently added to `token1BalanceScaled` (or `token0BalanceScaled` for the mirror direction). The same pattern holds in `buyToken1InBinSpecifiedOut`, `buyToken0InBinSpecifiedIn`, and `buyToken1InBinSpecifiedIn`. [2](#0-1) [3](#0-2) [4](#0-3) 

**How share redemption works**

`removeLiquidity` redeems at the current bin balance, proportional to shares held:

```solidity
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
``` [5](#0-4) 

There is no exit fee, no minimum holding period, and no lock. Tokens are transferred out immediately. [6](#0-5) 

**How `addLiquidity` prices new shares into an existing bin**

When a bin already has shares, the cost to mint new shares is proportional to the current bin balance (ceiling-rounded):

```solidity
amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
``` [7](#0-6) 

**The sandwich**

Suppose bin `b` has `T1 = 100` scaled token1, `S = 1000` total shares, and a large swap is pending that will deposit `F = 10` scaled token1 as LP fee.

1. **Front-run** — attacker calls `addLiquidity` for `S_a = 1000` shares, paying `ceil(100 × 1000 / 1000) = 100` scaled token1. Bin now has `T1 = 200`, `S = 2000`.
2. **Victim swap executes** — LP fee `F = 10` is added. Bin now has `T1 = 210`, `S = 2000`.
3. **Back-run** — attacker calls `removeLiquidity` for `1000` shares, receiving `floor(210 × 1000 / 2000) = 105` scaled token1.

Attacker profit: `105 − 100 = 5` scaled token1 (half the LP fee), at zero risk once the swap is confirmed.

The `addLiquidity` and `removeLiquidity` functions are not gated by `whenNotPaused`, so this works even when swaps are paused for other users. [8](#0-7) 

### Impact Explanation

Existing LPs lose a portion of every LP fee they are owed. The attacker captures that portion without bearing any inventory risk beyond the brief window between the two liquidity transactions. On high-volume pools or large individual swaps the stolen fee can be material. Because `addLiquidity` / `removeLiquidity` are not blocked by pause, the attack surface persists even during operational pauses.

### Likelihood Explanation

Any unprivileged address can execute this. It requires only mempool visibility (standard on Ethereum/Base) and capital for the front-run deposit (or a flash loan if the attacker controls both the front-run and back-run in the same block via a builder). No special roles, no malicious setup, and no non-standard tokens are needed.

### Recommendation

Implement an exit fee charged on `removeLiquidity` that is proportional to the LP fee rate and credited to the bin (or to a reserve). The fee should be large enough to make a 1-block deposit-and-withdraw cycle unprofitable. Concretely, if the maximum LP fee rate is `f`, the exit fee should exceed `f / (1 + f)` of the withdrawn amount. This is the approach discussed in the Union Finance issue: an exit fee paid to the reserve factor makes flash-deposit and short-duration sandwiches unprofitable without requiring TWAP or duration-based accounting.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Foundry test skeleton — fill in pool/token setup from MetricOmmPool.base.t.sol

contract SandwichLpFeeTest is MetricOmmPoolBaseTest {
    function test_lpFeeSandwich() public {
        // 1. Existing LP seeds bin 0 with liquidity
        int8 bin = 0;
        uint104 existingShares = 10_000;
        _doAddLiquidity(0 /*lpIndex*/, DEFAULT_SALT, _createDeltaArray(bin, existingShares));

        // Record attacker token1 balance before
        address attacker = users[1];
        uint256 t1Before = token1.balanceOf(attacker);

        // 2. Attacker front-runs: add equal shares to bin 0
        uint104 attackShares = 10_000;
        _doAddLiquidity(1 /*attackerIndex*/, DEFAULT_SALT + 1, _createDeltaArray(bin, attackShares));

        // 3. Victim swap executes: sell token0 for token1 (deposits token0 LP fee into bin 0)
        uint128 swapAmount = 5_000;
        _doSwap(2 /*swapperIndex*/, true /*zeroForOne*/, int128(swapAmount), 0);

        // 4. Attacker back-runs: remove all shares
        _doRemoveLiquidity(1, DEFAULT_SALT + 1, _createDeltaArray(bin, attackShares));

        uint256 t1After = token1.balanceOf(attacker);
        // Attacker receives more token1 than deposited — profit equals share of LP fee
        assertGt(t1After, t1Before, "Attacker should profit from LP fee sandwich");
    }
}
```

The attacker's profit equals `(attackShares / totalSharesAfterAdd) × lpFeeFromSwap`, which is always positive when `lpFeeFromSwap > 0` and there is no exit fee.

### Citations

**File:** metric-core/contracts/libraries/SwapMath.sol (L409-415)
```text
      uint256 feeAmountScaled = Math.ceilDiv(amountInScaled * currBinBuyFeeX64, ONE_X64);
      amountInScaled += feeAmountScaled;
      uint256 protocolFeeAmountScaled = (feeAmountScaled * spreadFeeE6) / 1e6;

      binState.token0BalanceScaled -= amountOutScaled.toUint104();
      binState.token1BalanceScaled =
        (uint256(binState.token1BalanceScaled) + amountInScaled - protocolFeeAmountScaled).toUint104();
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L495-501)
```text
      uint256 feeAmountScaled = Math.ceilDiv(amountInScaled * currBinSellFeeX64, ONE_X64);
      amountInScaled += feeAmountScaled;
      uint256 protocolFeeAmountScaled = (feeAmountScaled * spreadFeeE6) / 1e6;

      binState.token1BalanceScaled -= amountOutScaled.toUint104();
      binState.token0BalanceScaled =
        (uint256(binState.token0BalanceScaled) + amountInScaled - protocolFeeAmountScaled).toUint104();
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L636-641)
```text
      uint256 token1FeeScaled = lpFeeScaledFromGrossInput(totalIn1Scaled, currBinBuyFeeX64, onePlusBuyFeeX64);

      uint256 protocolFeeAmountScaled = (token1FeeScaled * spreadFeeE6) / 1e6;
      binState.token0BalanceScaled -= out0Scaled.toUint104();
      binState.token1BalanceScaled =
        uint256((binState.token1BalanceScaled) + totalIn1Scaled - protocolFeeAmountScaled).toUint104();
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L779-781)
```text
      binState.token1BalanceScaled -= out1Scaled.toUint104();
      binState.token0BalanceScaled =
        (uint256(binState.token0BalanceScaled) + totalIn0Scaled - protocolFeeAmountScaled).toUint104();
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L109-110)
```text
            amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
            amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L205-206)
```text
          uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
          uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-212)
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

  /// @inheritdoc IMetricOmmPoolActions
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }
```
