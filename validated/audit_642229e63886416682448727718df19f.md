### Title
Missing Slippage Protection in `removeLiquidity` Allows LPs to Receive Less Than Expected - (File: metric-core/contracts/MetricOmmPool.sol, metric-core/contracts/libraries/LiquidityLib.sol)

---

### Summary

`MetricOmmPool.removeLiquidity` and `LiquidityLib.removeLiquidity` accept no minimum-output parameters. An LP's token proceeds are computed from live bin balances at execution time, so any swap that executes before the LP's transaction can shift bin composition and reduce the value received — with no on-chain guard to revert the transaction.

---

### Finding Description

`removeLiquidity` burns a caller-specified number of shares per bin and returns the proportional token amounts based on the bin's current `token0BalanceScaled` and `token1BalanceScaled`:

```solidity
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
``` [1](#0-0) 

These scaled amounts are then converted to native token amounts and transferred directly to the owner:

```solidity
(amount0Removed, amount1Removed) =
    _deltasScaledToExternal(totalToken0ToRemoveScaled, totalToken1ToRemoveScaled, ctx, Math.Rounding.Floor);

if (amount0Removed > 0) { IERC20(ctx.token0).safeTransfer(owner, amount0Removed); }
if (amount1Removed > 0) { IERC20(ctx.token1).safeTransfer(owner, amount1Removed); }
``` [2](#0-1) 

The pool-level entry point accepts no minimum-amount parameters:

```solidity
function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
``` [3](#0-2) 

The periphery `MetricOmmPoolLiquidityAdder` provides `addLiquidity` wrappers with `maxAmountToken0`/`maxAmountToken1` caps enforced in the callback, but provides **no `removeLiquidity` wrapper** with equivalent minimum-output protection. [4](#0-3) 

By contrast, the router's swap functions do enforce slippage: `exactInputSingle` checks `amountOutMinimum` and `exactOutputSingle` checks `amountInMaximum`. [5](#0-4) [6](#0-5) 

`removeLiquidity` has no equivalent protection anywhere in the stack.

---

### Impact Explanation

**Impact: Medium**

An LP removing liquidity from the active bin (bin 0, or any bin that is partially filled) submits a transaction expecting a specific mix of token0 and token1. If swaps execute before the LP's transaction, the bin composition shifts — for example, a large buy of token0 drains all token0 from the bin, leaving only token1. The LP then receives only token1 with no on-chain revert. The LP suffers a composition loss (receiving the less-preferred token) with no recourse. In the worst case, a sandwich attacker can:

1. Swap to drain token0 from the active bin (paying spread fees).
2. Let the LP's `removeLiquidity` execute — LP receives only token1.
3. Swap back to restore the bin, profiting from the spread if the gain exceeds fees.

Because Metric OMM is oracle-anchored, the total value of bin assets is approximately constant at oracle price, so the loss is bounded by the fee cost of the sandwich rather than unlimited slippage. This makes the severity **Medium** (meaningful but bounded loss of LP principal composition, not total value destruction).

---

### Likelihood Explanation

**Likelihood: Low**

Requires a pending `removeLiquidity` transaction to be visible in the mempool and a profitable sandwich opportunity (gain > 2× spread fee). On high-throughput chains (Base, HyperEVM) with private mempools this is less likely, but on Ethereum mainnet with public mempools it is a realistic attack vector for large LP withdrawals from active bins.

---

### Recommendation

Add `minAmount0` and `minAmount1` parameters to `removeLiquidity` at the pool level, or provide a periphery wrapper (analogous to `MetricOmmPoolLiquidityAdder` for deposits) that enforces minimum output amounts:

```solidity
function removeLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 minAmount0,   // <-- add
    uint256 minAmount1,   // <-- add
    bytes calldata extensionData
) external nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
{
    // ... existing logic ...
    if (amount0Removed < minAmount0 || amount1Removed < minAmount1)
        revert InsufficientOutput(amount0Removed, amount1Removed, minAmount0, minAmount1);
}
```

Alternatively, add a periphery `removeLiquidity` helper on `MetricOmmPoolLiquidityAdder` (or a new contract) that wraps the core call and reverts on insufficient output, mirroring the `maxAmountToken0`/`maxAmountToken1` pattern already used for `addLiquidity`.

---

### Proof of Concept

1. LP holds 10,000 shares in bin 0 (active bin, 50% token0 / 50% token1 at current cursor).
2. LP submits `removeLiquidity` expecting ~5,000 units of token0 and ~5,000 units of token1.
3. Attacker front-runs with a large `swap` (token1 → token0, exact input), draining all token0 from bin 0. Bin 0 now holds 0 token0 and ~10,000 token1.
4. LP's `removeLiquidity` executes: `amount0Scaled = 0 * 10000 / totalShares = 0`, `amount1Scaled = 10000 * 10000 / totalShares ≈ 10000`. LP receives 0 token0 and ~10,000 token1.
5. Attacker back-runs with a swap (token0 → token1) to restore position.
6. LP received only token1 — no on-chain check reverted the transaction. [7](#0-6) [8](#0-7)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L83-83)
```text
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L145-145)
```text
    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
```
