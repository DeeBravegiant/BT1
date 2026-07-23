Audit Report

## Title
Missing `address(0)` owner validation in `MetricOmmPool.addLiquidity` permanently locks deposited tokens - (File: metric-core/contracts/MetricOmmPool.sol)

## Summary
`MetricOmmPool.addLiquidity` accepts any `owner` address including `address(0)` without a zero-address guard. Because `removeLiquidity` enforces `msg.sender == owner` and the EVM guarantees `msg.sender != address(0)`, any tokens deposited under `address(0)` as position owner are permanently irrecoverable from the pool.

## Finding Description
`MetricOmmPool.addLiquidity` (L182–196) accepts an arbitrary `owner` parameter and credits LP shares to `_positionBinShares[keccak256(abi.encode(owner, salt, binIdx))]` and increments `_binTotalShares[binIdx]` with no zero-address check on `owner`. [1](#0-0) 

`removeLiquidity` (L206) enforces:

```solidity
if (msg.sender != owner) revert NotPositionOwner();
``` [2](#0-1) 

Because the EVM guarantees `msg.sender != address(0)` for every external call, this check unconditionally reverts when `owner == address(0)`, making the deposited tokens permanently unrecoverable.

The periphery `MetricOmmPoolLiquidityAdder._validateOwner` (L247–249) guards against this:

```solidity
function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
}
``` [3](#0-2) 

However, the periphery is not the only entry point. Any EOA or contract can call `MetricOmmPool.addLiquidity` directly, bypassing the periphery guard entirely. [4](#0-3) 

## Impact Explanation
Tokens deposited under `owner = address(0)` are permanently locked inside the pool. The depositor suffers a direct, total loss of the principal transferred via the `metricOmmModifyLiquidityCallback`. Additionally, `_binTotalShares` is inflated by the orphaned shares, diluting the fractional claim of every other LP in that bin. This constitutes a direct loss of user principal and broken core liquidity functionality, meeting the Critical/High impact threshold.

## Likelihood Explanation
The trigger is reachable by any unprivileged caller who interacts with the core pool directly rather than through the periphery router. Integrators building custom routers, scripts, or on-chain adapters that call `MetricOmmPool.addLiquidity` directly and pass `address(0)` as `owner` (e.g., as a placeholder or by omission) will silently lose funds with no on-chain warning. The periphery guard creates a false sense of security that the core is also protected.

## Recommendation
Add a zero-address check at the top of `MetricOmmPool.addLiquidity`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
+   require(owner != address(0), InvalidPositionOwner());
    if (deltas.binIdxs.length == 0) return (0, 0);
    ...
}
```

The `InvalidPositionOwner` error already exists in the periphery; it should be promoted to the core pool interface or an equivalent error added there.

## Proof of Concept

```solidity
// Caller interacts with core pool directly, bypassing periphery
pool.addLiquidity(
    address(0),   // owner — zero address, no revert
    salt,
    deltas,
    callbackData, // callback pays real tokens
    ""
);

// Tokens are now in the pool, shares credited to address(0).
// Attempt to recover:
pool.removeLiquidity(
    address(0),   // owner
    salt,
    deltas,
    ""
);
// Always reverts: NotPositionOwner()
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
