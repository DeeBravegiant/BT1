### Title
No Slippage Protection for `removeLiquidity` Exposes LPs to Sandwich Attacks on Active-Bin Withdrawals - (`metric-core/contracts/MetricOmmPool.sol`, `metric-core/contracts/libraries/LiquidityLib.sol`)

---

### Summary

`MetricOmmPool.removeLiquidity` accepts no minimum-output parameters (`minAmount0Out`, `minAmount1Out`) and no deadline. The token amounts returned to the LP are computed purely from the live bin balances at execution time. An attacker can sandwich the removal transaction to shift the active bin's token composition before the LP's burn executes, causing the LP to receive a materially worse token mix than expected, with no on-chain guard to revert the transaction.

---

### Finding Description

`addLiquidity` is protected by the `MetricOmmPoolLiquidityAdder` periphery, which enforces `maxAmountToken0`, `maxAmountToken1`, and cursor-position bounds (`minimalCurBin`/`maximalCurBin`) before any tokens are pulled from the caller. [1](#0-0) 

`removeLiquidity`, by contrast, has no periphery wrapper at all — confirmed by a search of all `metric-periphery/**/*.sol` files — and the core function signature carries no slippage parameters:

```solidity
function removeLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,   // only bin indices + shares to burn
    bytes calldata extensionData
) external returns (uint256 amount0Removed, uint256 amount1Removed);
``` [2](#0-1) 

Inside `LiquidityLib.removeLiquidity`, the amounts returned are computed as a straight proportional share of the **current** bin balances at the moment of execution:

```solidity
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
``` [3](#0-2) 

These values are then transferred directly to `owner` with no floor check: [4](#0-3) 

In the active bin (bin index = `curBinIdx`), both `token0BalanceScaled` and `token1BalanceScaled` are non-zero and their ratio changes continuously as swaps move the cursor position within the bin. A swap that pushes the cursor toward the token0-heavy end of the bin drains token1 from the bin; a swap in the opposite direction drains token0. Because `removeLiquidity` has no minimum-output guard, the LP cannot prevent execution at an arbitrarily bad composition.

---

### Impact Explanation

An LP removing shares from the active bin receives whatever token mix exists at execution time. A sandwich attacker can:

1. **Front-run**: execute a large swap that moves the cursor within the active bin, converting most of one token into the other (e.g., draining token0 and filling with token1).
2. **LP's removal executes**: the LP receives a composition dominated by the cheaper-to-acquire token, losing value relative to the pre-sandwich composition.
3. **Back-run**: the attacker swaps back, profiting from the round-trip minus fees.

The loss per removal is bounded by the spread fee the attacker must pay, but for large LP positions in high-spread pools the net loss to the LP can be significant. There is no on-chain mechanism for the LP to express a minimum acceptable output or a deadline, so the attack is repeatable and requires no privileged access.

---

### Likelihood Explanation

- `removeLiquidity` is a public, permissionless function callable by any position owner.
- Active-bin positions are the normal case for LPs who deposit across a range that includes the current price.
- The attack requires only a standard sandwich (two swaps in the same block), which is routine on any chain with a public mempool.
- No special setup, governance action, or malicious token is needed.

---

### Recommendation

Add minimum-output parameters to `removeLiquidity` at the pool level, or provide a periphery wrapper (analogous to `MetricOmmPoolLiquidityAdder`) that enforces them:

```solidity
function removeLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 minAmount0Out,   // revert if amount0Removed < minAmount0Out
    uint256 minAmount1Out,   // revert if amount1Removed < minAmount1Out
    uint256 deadline,        // revert if block.timestamp > deadline
    bytes calldata extensionData
) external returns (uint256 amount0Removed, uint256 amount1Removed);
```

This mirrors the protection already present for swaps (`amountOutMinimum` in `exactInputSingle`) and for liquidity additions (`maxAmountToken0`/`maxAmountToken1` in `MetricOmmPoolLiquidityAdder`). [5](#0-4) [1](#0-0) 

---

### Proof of Concept

```
Setup:
  - Pool with token0 (WETH) / token1 (USDC), active bin = 0
  - LP holds 10,000 shares in bin 0; bin 0 currently holds 50% token0 / 50% token1
  - LP submits removeLiquidity(owner, salt, [{binIdx: 0, shares: 10000}], "")
    expecting ~500 WETH + ~500 USDC

Attack (same block, before LP tx):
  1. Attacker calls pool.swap(zeroForOne=true, largeAmount)
     → cursor moves to the token0-heavy end of bin 0
     → bin 0 now holds ~90% token0 / ~10% token1

LP tx executes:
  2. amount0Scaled = token0BalanceScaled * 10000 / totalShares  → ~900 WETH
     amount1Scaled = token1BalanceScaled * 10000 / totalShares  → ~100 USDC
     LP receives 900 WETH + 100 USDC instead of 500 WETH + 500 USDC
     At current price (WETH < USDC), LP receives less total value.
     No minAmount1Out guard → transaction does not revert.

Back-run:
  3. Attacker calls pool.swap(zeroForOne=false) to restore price, capturing the spread.
```

The LP has no recourse because `removeLiquidity` accepts no minimum-output or deadline arguments. [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L161-170)
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
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L204-247)
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L81-83)
```text
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);
```
