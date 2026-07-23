### Title
Missing Slippage Protection When Removing Liquidity - (File: `metric-core/contracts/MetricOmmPool.sol`, `metric-core/contracts/libraries/LiquidityLib.sol`)

### Summary

The `removeLiquidity` function in `MetricOmmPool` accepts no minimum-output parameters (`minAmount0`, `minAmount1`). The amounts returned are computed directly from live bin state at execution time, which can change drastically due to swaps between transaction submission and execution. No periphery wrapper adds this protection either — `MetricOmmPoolLiquidityAdder` only exposes `addLiquidity` paths.

### Finding Description

`MetricOmmPool.removeLiquidity` delegates to `LiquidityLib.removeLiquidity`, which computes each token amount as a proportional share of the bin's current scaled balances:

```solidity
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
``` [1](#0-0) 

These values depend entirely on the live `token0BalanceScaled` / `token1BalanceScaled` of each bin at the moment of execution. In the Metric OMM bin model, swaps move the price cursor across bins and convert their token composition: a bin that held only token0 (above the current price) becomes a token1-only bin after the price cursor sweeps through it. A user who deposited token0 into such a bin will receive only token1 on withdrawal if a swap has moved the cursor past that bin.

The pool signature is:

```solidity
function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
``` [2](#0-1) 

There are no `minAmount0` or `minAmount1` parameters. After computing the amounts, tokens are transferred directly to `owner` with no post-condition check:

```solidity
if (amount0Removed > 0) { IERC20(ctx.token0).safeTransfer(owner, amount0Removed); }
if (amount1Removed > 0) { IERC20(ctx.token1).safeTransfer(owner, amount1Removed); }
``` [3](#0-2) 

The periphery `MetricOmmPoolLiquidityAdder` provides slippage protection only for deposits (`maxAmountToken0`, `maxAmountToken1` caps enforced in the callback), but exposes **no** `removeLiquidity` function at all: [4](#0-3) 

Users must call `pool.removeLiquidity()` directly with no slippage guard available anywhere in the stack.

### Impact Explanation

A liquidity provider who deposited token0 into a bin above the current price cursor can receive exclusively token1 (zero token0) if a swap moves the cursor past that bin before their withdrawal executes. The total scaled value is approximately preserved at oracle prices, but the token composition can flip entirely. The user:

- Receives a token they did not want (e.g., cannot repay a token0-denominated debt)
- Must execute an additional swap to convert back, incurring fees and further slippage
- Has no on-chain mechanism to revert the transaction if the output falls below their acceptable threshold

This is a direct loss of the expected asset, not merely a value loss, and it is reachable by any pending mempool transaction being front-run by a swap at oracle prices.

### Likelihood Explanation

The trigger is any swap that moves the price cursor across a bin in which the victim holds shares. This is a normal, permissionless pool operation. In a multi-bin pool with active trading, price cursor movement across bins is routine. No privileged access is required; any user can execute the swap. The victim's `removeLiquidity` transaction is visible in the mempool, making targeted front-running straightforward.

### Recommendation

Add `minAmount0` and `minAmount1` parameters to `removeLiquidity` in both the pool interface and `LiquidityLib`, and revert if the computed amounts fall below the caller's minimums:

```solidity
function removeLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 minAmount0,
    uint256 minAmount1,
    bytes calldata extensionData
) external nonReentrant(PoolActions.REMOVE_LIQUIDITY) returns (uint256 amount0Removed, uint256 amount1Removed) {
    ...
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(...);
    if (amount0Removed < minAmount0 || amount1Removed < minAmount1) revert SlippageExceeded();
    ...
}
```

Additionally, expose a `removeLiquidity` wrapper in `MetricOmmPoolLiquidityAdder` (or a dedicated periphery contract) that enforces these bounds, mirroring the existing `maxAmountToken0`/`maxAmountToken1` pattern used for deposits.

### Proof of Concept

1. Pool has bins at indices -1, 0, 1. Current price cursor is at bin 0.
2. Alice calls `addLiquidity` for bin 1 (above current price), depositing token0.
3. Alice submits `removeLiquidity` for bin 1, expecting to receive token0.
4. Bob (or a MEV bot) front-runs with a large `swap(zeroForOne=false)` that moves the cursor from bin 0 to bin 1 and beyond, converting bin 1's token0 balance entirely to token1.
5. Alice's `removeLiquidity` executes: `binState.token0BalanceScaled == 0`, `binState.token1BalanceScaled > 0`. Alice receives only token1 and zero token0.
6. No revert occurs because there is no minimum-output check anywhere in the call path.

The `LiquidityLib.removeLiquidity` loop at lines 205–206 computes `amount0Scaled = 0` when `binState.token0BalanceScaled == 0`, and the transfer block at lines 242–247 simply skips the token0 transfer. The transaction succeeds silently with Alice receiving a composition she never consented to. [5](#0-4)

### Citations

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L161-251)
```text
  function removeLiquidity(
    PoolContext memory ctx,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    BinTotals storage binTotals,
    mapping(int256 => BinState) storage binStates,
    mapping(int256 => uint256) storage binTotalShares,
    mapping(bytes32 => uint256) storage positionBinShares
  ) public returns (uint256 amount0Removed, uint256 amount1Removed) {
    unchecked {
      uint256 length = deltas.binIdxs.length;
      if (length == 0) return (0, 0);

      uint256 totalToken0ToRemoveScaled = 0;
      uint256 totalToken1ToRemoveScaled = 0;

      BinBalanceDelta[] memory binBalanceDeltas = new BinBalanceDelta[](length);

      for (uint256 i = 0; i < length; i++) {
        int256 binIdx = deltas.binIdxs[i];
        uint256 sharesToRemove = deltas.shares[i];

        if (binIdx < ctx.lowestBin || binIdx > ctx.highestBin) {
          revert IMetricOmmPoolActions.InvalidBinIndex(binIdx);
        }
        if (sharesToRemove == 0) continue;

        {
          // safe because -128 <= LOWEST_BIN <= HIGHEST_BIN <= 127 (enforced by factory)
          // forge-lint: disable-next-line(unsafe-typecast)
          bytes32 posKey = _positionBinKey(owner, salt, int8(binIdx));
          uint256 binTotalSharesVal = binTotalShares[binIdx];
          uint256 userShares = positionBinShares[posKey];

          if (userShares < sharesToRemove) {
            revert IMetricOmmPoolActions.InsufficientLiquidity(sharesToRemove, userShares);
          }
          uint256 newUserShares = userShares - sharesToRemove;
          if (newUserShares > 0 && newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }

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

          totalToken0ToRemoveScaled += amount0Scaled;
          totalToken1ToRemoveScaled += amount1Scaled;

          binBalanceDeltas[i] = BinBalanceDelta({
            // safe because amount0Scaled is bounded by uint104 bin balances.
            // forge-lint: disable-next-line(unsafe-typecast)
            delta0Scaled: -int256(amount0Scaled),
            // safe because amount1Scaled is bounded by uint104 bin balances.
            // forge-lint: disable-next-line(unsafe-typecast)
            delta1Scaled: -int256(amount1Scaled)
          });
        }
      }

      if (totalToken0ToRemoveScaled > 0) {
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken0 = uint128(uint256(binTotals.scaledToken0) - totalToken0ToRemoveScaled);
      }
      if (totalToken1ToRemoveScaled > 0) {
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken1 = uint128(uint256(binTotals.scaledToken1) - totalToken1ToRemoveScaled);
      }

      (amount0Removed, amount1Removed) =
        _deltasScaledToExternal(totalToken0ToRemoveScaled, totalToken1ToRemoveScaled, ctx, Math.Rounding.Floor);

      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
      if (amount1Removed > 0) {
        IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
      }

      emit IMetricOmmPoolActions.LiquidityRemoved(owner, salt, deltas.binIdxs, binBalanceDeltas, deltas.shares);
    }
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

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L75-174)
```text
  // ============ Mutating: Liquidity ============

  /// @notice Add liquidity to `owner` with explicit per-bin shares and max token caps.
  /// @param pool Target pool address.
  /// @param owner Position owner recorded in pool storage.
  /// @param salt Position salt in the owner key-space.
  /// @param deltas Shares per bin.
  /// @param maxAmountToken0 Max token0 allowed to be pulled from caller.
  /// @param maxAmountToken1 Max token1 allowed to be pulled from caller.
  /// @param extensionData Opaque bytes forwarded to liquidity extensions (beforeAddLiquidity / afterAddLiquidity).
  /// @return amount0Added Token0 added.
  /// @return amount1Added Token1 added.
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable returns (uint256 amount0Added, uint256 amount1Added);

  /// @notice Add liquidity for caller-owned position with explicit shares and max token caps.
  /// @param pool Target pool address.
  /// @param salt Position salt in caller key-space.
  /// @param deltas Shares per bin.
  /// @param maxAmountToken0 Max token0 allowed to be pulled from caller.
  /// @param maxAmountToken1 Max token1 allowed to be pulled from caller.
  /// @param extensionData Opaque bytes forwarded to liquidity extensions (beforeAddLiquidity / afterAddLiquidity).
  /// @return amount0Added Token0 added.
  /// @return amount1Added Token1 added.
  function addLiquidityExactShares(
    address pool,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable returns (uint256 amount0Added, uint256 amount1Added);

  /// @notice Add liquidity from weight vector by probing and scaling to fit max caps.
  /// @dev Deposit composition follows the pool cursor at probe time. Use cursor bounds from slot0 to fail closed
  ///      when the pool state has been moved away from the price the caller signed for.
  /// @param pool Target pool address.
  /// @param owner Position owner recorded in pool storage.
  /// @param salt Position salt in owner key-space.
  /// @param weightDeltas Weight vector used for probe then scaled to integer shares.
  /// @param maxAmountToken0 Max token0 allowed to be pulled from caller.
  /// @param maxAmountToken1 Max token1 allowed to be pulled from caller.
  /// @param minimalCurBin Minimum allowed curBinIdx from slot0; use type(int8).min to disable lower bin bound.
  /// @param minimalPosition Minimum curPosInBin when curBinIdx equals minimalCurBin.
  /// @param maximalCurBin Maximum allowed curBinIdx from slot0; use type(int8).max to disable upper bin bound.
  /// @param maximalPosition Maximum curPosInBin when curBinIdx equals maximalCurBin; use type(uint104).max when
  ///        unconstrained at maximalCurBin.
  /// @param extensionData Opaque bytes forwarded to liquidity extensions (beforeAddLiquidity / afterAddLiquidity).
  /// @return amount0Added Token0 added.
  /// @return amount1Added Token1 added.
  function addLiquidityWeighted(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition,
    bytes calldata extensionData
  ) external payable returns (uint256 amount0Added, uint256 amount1Added);

  /// @notice Add liquidity from weight vector by probing and scaling to fit max caps for caller-owned position.
  /// @dev Deposit composition follows the pool cursor at probe time. Use cursor bounds from slot0 to fail closed
  ///      when the pool state has been moved away from the price the caller signed for.
  /// @param pool Target pool address.
  /// @param salt Position salt in caller key-space.
  /// @param weightDeltas Weight vector used for probe then scaled to integer shares.
  /// @param maxAmountToken0 Max token0 allowed to be pulled from caller.
  /// @param maxAmountToken1 Max token1 allowed to be pulled from caller.
  /// @param minimalCurBin Minimum allowed curBinIdx from slot0; use type(int8).min to disable lower bin bound.
  /// @param minimalPosition Minimum curPosInBin when curBinIdx equals minimalCurBin.
  /// @param maximalCurBin Maximum allowed curBinIdx from slot0; use type(int8).max to disable upper bin bound.
  /// @param maximalPosition Maximum curPosInBin when curBinIdx equals maximalCurBin; use type(uint104).max when
  ///        unconstrained at maximalCurBin.
  /// @param extensionData Opaque bytes forwarded to liquidity extensions (beforeAddLiquidity / afterAddLiquidity).
  /// @return amount0Added Token0 added.
  /// @return amount1Added Token1 added.
  function addLiquidityWeighted(
    address pool,
    uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition,
    bytes calldata extensionData
  ) external payable returns (uint256 amount0Added, uint256 amount1Added);
}
```
