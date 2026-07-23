### Title
Missing Zero Address Validation on `owner` in `addLiquidity` Permanently Locks Deposited Tokens - (File: metric-core/contracts/MetricOmmPool.sol)

### Summary

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address without checking for `address(0)`. Because `removeLiquidity` enforces `msg.sender == owner`, and `msg.sender` can never be `address(0)`, any shares minted to the zero-address position key are permanently irrecoverable, and the tokens deposited via callback are permanently locked in the pool.

### Finding Description

`MetricOmmPool.addLiquidity` passes `owner` directly into `LiquidityLib.addLiquidity` with no zero-address guard: [1](#0-0) 

Inside `LiquidityLib.addLiquidity`, the position key is computed as `_positionBinKey(owner, salt, binIdx)` and shares are written to `positionBinShares[posKey]`. When `owner == address(0)`, shares are credited to the zero-address slot: [2](#0-1) 

After shares are minted, the callback pulls real tokens from `msg.sender` into the pool: [3](#0-2) 

`removeLiquidity` then enforces `msg.sender == owner`: [4](#0-3) 

Since `msg.sender` can never equal `address(0)`, the zero-address position can never be unwound. The deposited tokens are permanently locked.

The periphery contract `MetricOmmPoolLiquidityAdder` does include a `_validateOwner` guard: [5](#0-4) 

However, this check exists only in the periphery. Any caller interacting with the core pool directly — including integrators, routers, or other contracts using the documented operator pattern (`msg.sender` pays, `owner` receives shares) — bypasses this protection entirely.

### Impact Explanation

A caller who passes `owner = address(0)` to `addLiquidity` loses all deposited tokens permanently. The tokens are transferred into the pool via the modify-liquidity callback, `binState` balances and `binTotalShares` are updated, but the corresponding position can never be removed. This is a direct, irreversible loss of user principal with no recovery path.

### Likelihood Explanation

The operator pattern is explicitly documented and supported: `msg.sender` pays but `owner` can differ. Any integrator, router, or contract that constructs the `owner` argument programmatically (e.g., from user-supplied calldata, a decoded address, or a default value) can accidentally pass `address(0)`. The core pool provides no guard, so the error is silent — the transaction succeeds, tokens are transferred, and the loss is only discovered when withdrawal is attempted.

### Recommendation

Add a zero-address check for `owner` in `MetricOmmPool.addLiquidity` before delegating to `LiquidityLib`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
+   if (owner == address(0)) revert InvalidPositionOwner();
    if (deltas.binIdxs.length == 0) return (0, 0);
    ...
}
```

Alternatively, add the check at the top of `LiquidityLib.addLiquidity` so it is enforced regardless of the call path.

### Proof of Concept

1. Attacker (or integrator) calls `pool.addLiquidity(address(0), salt, deltas, callbackData, "")` directly on the core pool.
2. `LiquidityLib.addLiquidity` computes `posKey = _positionBinKey(address(0), salt, binIdx)` and writes shares to `positionBinShares[posKey]`.
3. The callback fires, pulling real token amounts from `msg.sender` into the pool. Balances are verified and the call succeeds.
4. Caller attempts `pool.removeLiquidity(address(0), salt, deltas, "")` — this reverts with `NotPositionOwner` because `msg.sender != address(0)`.
5. No other address can ever satisfy `msg.sender == address(0)`. The deposited tokens are permanently locked.

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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
