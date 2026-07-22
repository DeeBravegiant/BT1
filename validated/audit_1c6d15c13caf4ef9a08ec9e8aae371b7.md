### Title
Unvalidated `owner` address in `addLiquidity` permanently locks LP principal — (`File: metric-core/contracts/MetricOmmPool.sol`)

### Summary

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address with no zero-address check at the pool level. LP shares are minted under the key `keccak256(abi.encode(owner, salt, bin))`. Because `removeLiquidity` enforces `msg.sender == owner`, passing `owner = address(0)` makes the position irrecoverable: `msg.sender` can never equal `address(0)`, so the underlying tokens paid into the pool via the callback are permanently locked.

### Finding Description

`MetricOmmPool.addLiquidity` passes `owner` directly to `LiquidityLib.addLiquidity` without any validation: [1](#0-0) 

Inside `LiquidityLib.addLiquidity`, the position key is derived and shares are written: [2](#0-1) 

After shares are recorded, the pool calls the callback to collect real tokens from the caller: [3](#0-2) 

`removeLiquidity` then enforces `msg.sender == owner` before transferring tokens back: [4](#0-3) 

And the token transfer destination is `owner`: [5](#0-4) 

The periphery `MetricOmmPoolLiquidityAdder` does guard against this with `_validateOwner`: [6](#0-5) 

However, this guard exists **only in the periphery**. The pool is a public external contract; any direct integrator (smart contract or EOA) can call `pool.addLiquidity(address(0), ...)` and bypass the periphery entirely. The pool itself has no corresponding check.

### Impact Explanation

When `owner = address(0)`:
1. LP shares are minted to the `address(0)` position key.
2. The caller pays real token0/token1 into the pool via the modify-liquidity callback — these tokens are credited to `binTotals` and `binState` balances.
3. `removeLiquidity` requires `msg.sender == owner`. Since `msg.sender` can never be `address(0)`, the position can never be unwound.
4. The tokens are permanently locked inside the pool's bin accounting, irrecoverable by anyone.

This is a direct, permanent loss of user principal with no recovery path.

### Likelihood Explanation

The pool is designed to be called directly by integrators (the periphery is optional). Any smart contract that calls `pool.addLiquidity` directly — a common integration pattern — and passes `address(0)` as `owner` (e.g., by mistake, by a misconfigured parameter, or by a front-end bug) triggers the loss. The external report's confirmed bug class (unvalidated address → locked funds) maps exactly here.

### Recommendation

Add a zero-address check inside `MetricOmmPool.addLiquidity` at the pool level, mirroring what the periphery already does:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (owner == address(0)) revert InvalidPositionOwner(); // ADD THIS
    if (deltas.binIdxs.length == 0) return (0, 0);
    ...
}
```

This ensures the invariant is enforced regardless of whether the caller routes through the periphery.

### Proof of Concept

```solidity
// Direct pool caller (no periphery)
contract MaliciousOrBuggyIntegrator is IMetricOmmModifyLiquidityCallback {
    function trigger(address pool, address token0, uint256 amount) external {
        // owner = address(0) — no revert at pool level
        LiquidityDelta memory delta = LiquidityDelta({
            binIdxs: new int256[](1),
            shares: new uint256[](1)
        });
        delta.binIdxs[0] = 0;
        delta.shares[0] = 10_000;

        // Tokens are pulled from this contract in the callback
        // Shares are minted to address(0)
        IMetricOmmPoolActions(pool).addLiquidity(address(0), 0, delta, "", "");
        // Tokens are now permanently locked; removeLiquidity(address(0),...) 
        // always reverts because msg.sender != address(0)
    }

    function metricOmmModifyLiquidityCallback(uint256 a0, uint256 a1, bytes calldata) external override {
        IERC20(token0).transfer(msg.sender, a0);
    }
}
```

### Citations

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L72-121)
```text
          bytes32 posKey = _positionBinKey(owner, salt, int8(binIdx));
          uint256 binTotalSharesVal = binTotalShares[binIdx];
          uint256 userShares = positionBinShares[posKey];

          uint256 newUserShares = userShares + sharesToAdd;
          if (newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }

          BinState storage binState = binStates[binIdx];

          uint256 amount0Scaled = 0;
          uint256 amount1Scaled = 0;
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L242-247)
```text
      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
      if (amount1Removed > 0) {
        IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
      }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
