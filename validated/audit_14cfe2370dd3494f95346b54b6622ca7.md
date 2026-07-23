Audit Report

## Title
Missing Zero-Address Check for `owner` in `addLiquidity` Permanently Locks LP Shares and Burns Caller Tokens - (File: metric-core/contracts/MetricOmmPool.sol)

## Summary
`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` address with no zero-address guard. Passing `owner = address(0)` mints LP shares to the zero address while pulling real tokens from the caller via the modify-liquidity callback. Because `removeLiquidity` enforces `msg.sender == owner`, and `address(0)` can never be `msg.sender`, the deposited position is permanently inaccessible and the caller's tokens are unrecoverable.

## Finding Description
`MetricOmmPool.addLiquidity` (lines 182–196) forwards `owner` directly into `LiquidityLib.addLiquidity` with no zero-address validation:

```solidity
function addLiquidity(
    address owner,   // ← no require(owner != address(0))
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
        _liquidityContext(), owner, salt, deltas, callbackData, ...
    );
```

Inside `LiquidityLib.addLiquidity`, shares are keyed under `_positionBinKey(owner, salt, int8(binIdx))` (line 72) and credited to `positionBinShares[posKey]` (line 121). When `owner = address(0)`, shares are stored at the `address(0)` key. The callback at line 147–148 fires on `msg.sender`, pulling real tokens from the caller regardless of the `owner` value.

`removeLiquidity` (lines 199–212) enforces:
```solidity
if (msg.sender != owner) revert NotPositionOwner();
```
Since `address(0)` can never be `msg.sender` in any EVM transaction, the position is permanently locked and the deposited tokens are irrecoverable.

The periphery wrapper `MetricOmmPoolLiquidityAdder` does validate via `_validateOwner(owner)`, and the test `test_exactShares_revertsOnZeroOwner` (line 232–238) confirms this guard exists at the periphery layer. However, the pool itself is a public external contract with no access control on `addLiquidity`, so any direct caller bypasses the periphery guard entirely.

## Impact Explanation
Direct, unrecoverable loss of user principal. The caller pays real token0 and/or token1 through the modify-liquidity callback, but the resulting LP shares are minted to `address(0)` and can never be redeemed. The loss equals the full token value deposited in that call. This breaks the core LP accounting invariant: LP claims must be redeemable by their owner.

## Likelihood Explanation
Medium-low. The pool is a public external contract with no access control on `addLiquidity`. Any smart contract integrator that calls the pool directly (rather than through `MetricOmmPoolLiquidityAdder`) and passes `owner = address(0)` — whether by bug, misconfiguration, or a malicious wrapper that has token approval from a victim — triggers the loss. The periphery protects users who route through it, but the pool surface is exposed.

## Recommendation
Add a zero-address guard at the top of `addLiquidity`, mirroring the periphery's own `_validateOwner` check:

```diff
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
+   if (owner == address(0)) revert InvalidPositionOwner();
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
```

## Proof of Concept
```solidity
contract BuggyAdder is IMetricOmmModifyLiquidityCallback {
    IERC20 token0; IERC20 token1; IMetricOmmPool pool;

    function exploit(LiquidityDelta calldata deltas) external {
        // owner = address(0) — no revert from the pool
        pool.addLiquidity(address(0), 0, deltas, "", "");
        // Tokens pulled from this contract via callback.
        // Shares at _positionBinShares[keccak(address(0), 0, binIdx)].
        // removeLiquidity(address(0), ...) requires msg.sender == address(0) → impossible.
    }

    function metricOmmModifyLiquidityCallback(
        int256 amount0Delta, int256 amount1Delta, bytes calldata
    ) external override {
        if (amount0Delta > 0) token0.transfer(msg.sender, uint256(amount0Delta));
        if (amount1Delta > 0) token1.transfer(msg.sender, uint256(amount1Delta));
    }
}
```

After `exploit` returns, tokens are inside the pool credited to bin totals, but the LP position at `(address(0), salt, binIdx)` is permanently inaccessible. The caller's token balance is reduced by the deposited amounts with no recourse. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-206)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
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

**File:** metric-periphery/test/MetricOmmPoolLiquidityAdder.t.sol (L232-238)
```text
  function test_exactShares_revertsOnZeroOwner() public {
    LiquidityDelta memory d = _deltaAbovePrice(4, 10_000);

    vm.prank(alice);
    vm.expectRevert(IMetricOmmPoolLiquidityAdder.InvalidPositionOwner.selector);
    helper.addLiquidityExactShares(address(pool), address(0), 11, d, type(uint256).max, type(uint256).max, "");
  }
```
