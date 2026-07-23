Audit Report

## Title
Missing `owner != address(0)` Guard in `MetricOmmPool.addLiquidity()` Permanently Locks LP Tokens — (`metric-core/contracts/MetricOmmPool.sol`)

## Summary
`MetricOmmPool.addLiquidity()` accepts an arbitrary `owner` address with no zero-address validation. If `owner == address(0)` is passed, LP shares are minted to the `(address(0), salt, binIdx)` storage key and deposited tokens are permanently locked, because `removeLiquidity()` enforces `msg.sender == owner`, which can never be satisfied for `address(0)`. The periphery guard in `MetricOmmPoolLiquidityAdder._validateOwner()` is bypassable since the core pool is a public contract callable directly.

## Finding Description
`MetricOmmPool.addLiquidity()` performs only two input checks before delegating to `LiquidityLib`: [1](#0-0) 

Neither check rejects `owner == address(0)`. `LiquidityLib.addLiquidity()` then mints shares into `positionBinShares` keyed by `_positionBinKey(owner, salt, binIdx)`: [2](#0-1) 

The only exit path for deposited tokens is `removeLiquidity()`, which enforces: [3](#0-2) 

Since `msg.sender` can never equal `address(0)` in a normal EVM transaction, `NotPositionOwner` reverts unconditionally for any attempt to remove a position owned by `address(0)`. The periphery `MetricOmmPoolLiquidityAdder` guards against this via `_validateOwner(owner)`: [4](#0-3) 

However, `MetricOmmPool` is a standalone public contract callable by any EOA or contract directly, making the periphery guard entirely bypassable.

## Impact Explanation
A caller who passes `owner == address(0)` to `MetricOmmPool.addLiquidity()` directly pays real `token0`/`token1` into the pool via the modify-liquidity callback, receives LP shares credited to the `(address(0), salt, binIdx)` storage key, and can never recover those tokens. `binTotals.scaledToken0`/`scaledToken1` are incremented so pool accounting remains consistent, but the underlying tokens are permanently unclaimable — a direct, irreversible loss of user principal. This constitutes a broken core liquidity flow causing loss of funds, meeting the allowed impact gate.

## Likelihood Explanation
The core pool is permissionless and callable by any EOA or contract. The periphery guard is not enforced at the pool level. Integrators building custom routers or contracts wrapping `addLiquidity()` may omit the zero-address check, especially since `IMetricOmmPoolActions` documents no such restriction. A single mistaken or malicious call is sufficient to trigger the loss. Likelihood is low (requires passing `address(0)` as `owner`), but impact is high (permanent, unrecoverable loss of deposited tokens), mapping to **Medium** severity under Sherlock thresholds.

## Recommendation
Add an explicit zero-address guard at the top of `MetricOmmPool.addLiquidity()`, mirroring the check already present in the periphery:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (owner == address(0)) revert InvalidPositionOwner();   // ← add this
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    ...
}
```

## Proof of Concept
```solidity
// Caller calls the core pool directly, bypassing the periphery guard.
pool.addLiquidity(
    address(0),   // owner — zero address, no revert
    uint80(0),    // salt
    deltas,       // valid bin/share delta
    callbackData, // callback pays token0/token1 into pool
    ""
);
// Tokens are now in the pool. Shares at key (address(0), 0, binIdx).

// Attempt to recover — always reverts:
pool.removeLiquidity(address(0), uint80(0), deltas, "");
// → NotPositionOwner (msg.sender != address(0) always)
// Tokens are permanently locked.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L72-76)
```text
          bytes32 posKey = _positionBinKey(owner, salt, int8(binIdx));
          uint256 binTotalSharesVal = binTotalShares[binIdx];
          uint256 userShares = positionBinShares[posKey];

          uint256 newUserShares = userShares + sharesToAdd;
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L64-67)
```text
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
```
