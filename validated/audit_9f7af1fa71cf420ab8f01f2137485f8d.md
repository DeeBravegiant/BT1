### Title
Missing Zero-Address Check for `owner` in `addLiquidity` Permanently Locks LP Tokens - (File: metric-core/contracts/MetricOmmPool.sol)

### Summary
`MetricOmmPool.addLiquidity` accepts `owner = address(0)` without reverting. Shares are credited to the zero-address position key, but `removeLiquidity` enforces `msg.sender == owner`, making the position irrecoverable. Any tokens deposited for a zero-address owner are permanently locked in the pool.

### Finding Description
`MetricOmmPool.addLiquidity` has no zero-address guard on the `owner` parameter:

```solidity
function addLiquidity(
    address owner,          // ← no address(0) check
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, ...
    );
``` [1](#0-0) 

Inside `LiquidityLib.addLiquidity`, the position key is derived from `owner`, shares are written to `positionBinShares[posKey]`, and tokens are pulled from the callback:

```solidity
bytes32 posKey = _positionBinKey(owner, salt, int8(binIdx));
...
positionBinShares[posKey] = newUserShares;
``` [2](#0-1) 

When `owner = address(0)`, the position is recorded under `keccak256(abi.encode(address(0), salt, bin))`. Recovery is impossible because `removeLiquidity` enforces `msg.sender == owner`:

```solidity
if (msg.sender != owner) revert NotPositionOwner();
``` [3](#0-2) 

No EOA or contract can set `msg.sender = address(0)`, so the position can never be removed. Additionally, `LiquidityLib.removeLiquidity` calls `safeTransfer(owner, ...)`, which would revert for standard ERC20 tokens if `owner = address(0)`, providing a second layer of permanent lock:

```solidity
if (amount0Removed > 0) {
    IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
}
``` [4](#0-3) 

The periphery contract `MetricOmmPoolLiquidityAdder` explicitly validates this case, confirming the protocol intends to prevent it — but the protection is absent at the core pool level:

```solidity
function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
}
``` [5](#0-4) 

### Impact Explanation
Any caller who invokes `addLiquidity` directly on the core pool with `owner = address(0)` permanently loses the deposited tokens. The tokens are transferred into the pool via the `metricOmmModifyLiquidityCallback` (pulled from `msg.sender`), the pool's `binTotals` and `binState` balances are updated, but the LP position is irrecoverable. This is a direct, permanent loss of user principal with no recovery path.

### Likelihood Explanation
The core pool is a public contract callable by any integrator or contract. Integrators building custom wrappers, contracts that use a default-zero address, or developers who misread the API can trigger this. The periphery's explicit `_validateOwner` guard demonstrates the protocol recognized this risk but only applied the fix one layer up.

### Recommendation

Add a zero-address check for `owner` in `MetricOmmPool.addLiquidity`:

```diff
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
+   if (owner == address(0)) revert InvalidOwner();
    if (deltas.binIdxs.length == 0) return (0, 0);
```

### Proof of Concept

1. Alice calls `MetricOmmPool.addLiquidity(address(0), salt, deltas, callbackData, "")` directly on the core pool.
2. `LiquidityLib.addLiquidity` computes `posKey = keccak256(abi.encode(address(0), salt, bin))` and writes shares there.
3. The callback fires, pulling Alice's tokens into the pool. `binTotals.scaledToken0/1` increases.
4. Alice attempts `removeLiquidity(address(0), salt, deltas, "")` — reverts with `NotPositionOwner` because `msg.sender != address(0)`.
5. No other address can call `removeLiquidity` for this position. Alice's tokens are permanently locked in the pool, contributing to `binTotals` but never claimable.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-194)
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
