Audit Report

## Title
No Slippage Protection in `removeLiquidity` Exposes LPs to Front-Running Token Composition Losses — (`metric-core/contracts/MetricOmmPool.sol`, `metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary
`removeLiquidity` in `MetricOmmPool.sol` accepts only a share-burn quantity per bin and returns whatever token amounts the bin currently holds proportionally, with no `minAmount0Out`/`minAmount1Out` guard. Because `msg.sender == owner` is enforced at line 206, LPs cannot route through a slippage-checking periphery wrapper. The periphery provides `maxAmountToken0`/`maxAmountToken1` caps for `addLiquidity` via `MetricOmmPoolLiquidityAdder` but no equivalent for `removeLiquidity`, leaving the removal path entirely unguarded against sandwich attacks.

## Finding Description
In `LiquidityLib.removeLiquidity`, the token-out amounts are computed directly from live storage:

```solidity
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
``` [1](#0-0) 

These storage values are directly mutated by every swap. In `SwapMath.sol`:

```solidity
binState.token0BalanceScaled -= out0Scaled.toUint104();
binState.token1BalanceScaled =
  uint256((binState.token1BalanceScaled) + totalIn1Scaled - protocolFeeAmountScaled).toUint104();
``` [2](#0-1) 

The `removeLiquidity` interface and implementation carry no min-out parameters: [3](#0-2) [4](#0-3) 

The `msg.sender == owner` check at line 206 is the critical blocker: it prevents an LP from routing through any periphery wrapper that could enforce minimum-out checks, since the wrapper's address would be `msg.sender` but not `owner`. [5](#0-4) 

By contrast, `MetricOmmPoolLiquidityAdder` enforces slippage caps for `addLiquidity`: [6](#0-5) 

No `MetricOmmPoolLiquidityRemover` contract exists in the periphery.


## Impact Explanation
An LP removing liquidity from a bin holding both tokens can be sandwich-attacked: an attacker front-runs with a swap that drains token0 from the target bin, causing the LP's `removeLiquidity` to receive near-zero token0 and inflated token1. If token0 is more valuable at the oracle price, the LP suffers a direct loss of principal with no on-chain recourse. The loss is bounded by the bin's full token0 balance times the LP's share fraction — material for large concentrated positions. This constitutes a direct loss of LP principal, meeting the Medium/High impact threshold.

## Likelihood Explanation
Any unprivileged trader can execute the front-run using a standard swap call. No special privilege is required. The attack is most profitable on pools with tight bid-ask spreads (e.g., stablecoin pairs) and concentrated single-bin LP positions. The LP has no on-chain mechanism to prevent it: `removeLiquidity` is called directly by the owner with no deadline, no min-out, and no periphery wrapper available due to the `msg.sender == owner` restriction. Likelihood is **Medium**: requires mempool visibility and a profitable spread condition, but no protocol-level barrier exists.

## Recommendation
1. Add `minAmount0Out` and `minAmount1Out` parameters to `removeLiquidity` in both `IMetricOmmPoolActions` and `MetricOmmPool`, reverting if computed amounts fall below the caller's floor.
2. Alternatively, relax the `msg.sender == owner` restriction to allow an approved operator/wrapper, then create a periphery `MetricOmmPoolLiquidityRemover` contract analogous to `MetricOmmPoolLiquidityAdder` that enforces minimum-out checks and an optional deadline.

## Proof of Concept
```
Setup:
  Bin 0: token0BalanceScaled = 1_000_000, token1BalanceScaled = 1_000_000
  binTotalShares[0] = 2_000_000
  Alice owns 1_000_000 shares in bin 0 (50%)

Alice submits removeLiquidity(owner=Alice, deltas={binIdxs:[0], shares:[1_000_000]})
  Expected: amount0Removed = 500_000, amount1Removed = 500_000

Attacker front-runs with swap (!zeroForOne, exact-out token0):
  Drains all token0 from bin 0 → token0BalanceScaled = 0, token1BalanceScaled ≈ 2_000_000

Alice's removeLiquidity executes (LiquidityLib.sol L205-206):
  amount0Scaled = 0 * 1_000_000 / 2_000_000 = 0
  amount1Scaled = 2_000_000 * 1_000_000 / 2_000_000 = 1_000_000

Alice receives: 0 token0, 1_000_000 token1
  If token0 price > token1 price, Alice has lost value with no recourse.
  Alice cannot route through a wrapper to add a min-out check because
  msg.sender != owner would revert (MetricOmmPool.sol L206).
```

### Citations

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L205-206)
```text
          uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
          uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L639-641)
```text
      binState.token0BalanceScaled -= out0Scaled.toUint104();
      binState.token1BalanceScaled =
        uint256((binState.token1BalanceScaled) + totalIn1Scaled - protocolFeeAmountScaled).toUint104();
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L172-174)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    returns (uint256 amount0Removed, uint256 amount1Removed);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L165-167)
```text
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }
```
