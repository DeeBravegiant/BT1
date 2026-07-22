### Title
Unchecked Zero `owner` Address in `addLiquidity` Permanently Locks LP Tokens - (File: metric-core/contracts/MetricOmmPool.sol)

### Summary

`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address with no zero-address guard. If `owner = address(0)` is supplied, the pool mints shares to the dead-key position, pulls real tokens from `msg.sender` via the callback, and then makes those tokens permanently irrecoverable because `removeLiquidity` enforces `msg.sender == owner`, which can never be satisfied for `address(0)`.

### Finding Description

`MetricOmmPool.addLiquidity` (line 182) accepts `address owner` and immediately delegates to `LiquidityLib.addLiquidity` without any zero-address check: [1](#0-0) 

Inside `LiquidityLib.addLiquidity`, the position key is computed as `keccak256(abi.encode(owner, salt, binIdx))` and shares are credited to that key: [2](#0-1) 

Tokens are then pulled from `msg.sender` via the modify-liquidity callback: [3](#0-2) 

`removeLiquidity` enforces `msg.sender == owner` before burning shares: [4](#0-3) 

Because `msg.sender` can never equal `address(0)`, the position minted to the zero-address key is permanently frozen. The tokens transferred into the pool during the callback are irrecoverable — they inflate `binTotals` and `binState.token(0|1)BalanceScaled` but can never be withdrawn.

The periphery contract `MetricOmmPoolLiquidityAdder` does guard against this: [5](#0-4) 

But the core pool is a public interface. Any integrator or router that calls `addLiquidity` directly on the pool — including the `addLiquidityWeighted` probe path which calls `pool.addLiquidity(owner, ...)` with a caller-supplied `owner` — bypasses this guard entirely. [6](#0-5) 

### Impact Explanation

Direct, permanent loss of user principal. Tokens deposited under `owner = address(0)` are credited to an unreachable position key. They remain inside the pool's accounting (`binTotals`, per-bin balances) and cannot be extracted via `removeLiquidity`, `collectFees`, or any other mechanism. The pool's LP accounting becomes insolvent relative to the locked shares: other LPs' proportional claims are diluted by the phantom position.

### Likelihood Explanation

Medium-Low. The core pool is a public contract; any integrator, router, or user calling it directly (not through `MetricOmmPoolLiquidityAdder`) can trigger this. The `addLiquidityWeighted` probe path in the periphery itself passes a caller-supplied `owner` to the core pool, meaning a future periphery extension or third-party router that omits the `_validateOwner` call would silently lock funds. A single mistaken or malicious call is sufficient.

### Recommendation

Add a zero-address guard at the top of `MetricOmmPool.addLiquidity` (and symmetrically in `LiquidityLib.addLiquidity`):

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
    require(owner != address(0), "MetricOmmPool: zero owner");
    ...
}
```

### Proof of Concept

```solidity
// Attacker or mistaken integrator calls the core pool directly
pool.addLiquidity(
    address(0),   // owner = zero address
    0,            // salt
    deltas,       // valid bin/share deltas
    callbackData, // callback that pays tokens
    ""
);
// Tokens are pulled from msg.sender via metricOmmModifyLiquidityCallback.
// Shares are minted to keccak256(abi.encode(address(0), 0, binIdx)).
// removeLiquidity(address(0), ...) always reverts NotPositionOwner
// because msg.sender != address(0) is always true.
// Tokens are permanently locked.
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L106-115)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
