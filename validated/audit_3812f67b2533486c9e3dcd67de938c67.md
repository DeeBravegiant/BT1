### Title
Free Share Minting in Drained Bins Dilutes Existing LP Claims — (`metric-core/contracts/libraries/LiquidityLib.sol`)

### Summary

`LiquidityLib.addLiquidity` skips the token-payment callback entirely when both computed token amounts are zero. A bin that has been fully drained by swaps (`token0BalanceScaled == 0` and `token1BalanceScaled == 0`) but still carries existing LP shares (`binTotalShares > 0`) satisfies this condition. An attacker can therefore mint an arbitrary number of shares in such a bin at zero cost, then withdraw a proportional share of the tokens that flow back into the bin when the price reverses — directly stealing from existing LPs.

### Finding Description

In `LiquidityLib.addLiquidity`, shares and bin-balance state are updated unconditionally inside the loop, and the token-payment callback is only invoked when the aggregate owed amount is non-zero:

```solidity
// shares and bin state written first (lines 114–121)
binState.token0BalanceScaled = (uint256(binState.token0BalanceScaled) + amount0Scaled).toUint104();
binState.token1BalanceScaled = (uint256(binState.token1BalanceScaled) + amount1Scaled).toUint104();
binTotalShares[binIdx] = binTotalSharesVal + sharesToAdd;
positionBinShares[posKey] = newUserShares;
``` [1](#0-0) 

Then, after the loop:

```solidity
if (amount0Added > 0 || amount1Added > 0) {
    // callback only fires here
    IMetricOmmModifyLiquidityCallback(msg.sender)
        .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
    ...
}
``` [2](#0-1) 

For a non-empty bin (`binTotalSharesVal > 0`) whose balances have been drained to zero by swaps, the per-share token amounts are:

```solidity
amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
// = ceilDiv(0 * sharesToAdd, binTotalSharesVal) = 0
amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
// = ceilDiv(0 * sharesToAdd, binTotalSharesVal) = 0
``` [3](#0-2) 

Both scaled totals remain zero, `amount0Added = 0`, `amount1Added = 0`, the callback guard is false, and the attacker's shares are committed to storage without any token transfer.

Bins above the current price hold only token0. After a sequence of `!zeroForOne` swaps exhausts a bin's token0, `token0BalanceScaled` reaches zero while `binTotalShares` remains positive (swaps never touch share accounting). The drained bin is a persistent on-chain state that any observer can detect. [4](#0-3) 

### Impact Explanation

When the price later reverses and `zeroForOne` swaps refill the drained bin with token0, the attacker's free shares entitle them to a proportional fraction of that incoming token0. Existing LPs who deposited real capital receive a smaller fraction than they are owed. The attacker can then call `removeLiquidity` and withdraw tokens they never paid for, constituting a direct theft of LP principal. The loss scales with the attacker's free share count relative to the pre-existing `binTotalShares`. [5](#0-4) 

### Likelihood Explanation

Bins being fully drained is a normal market event in any active pool — it occurs whenever the oracle price moves far enough that all liquidity in a bin is consumed by swaps. The attacker only needs to monitor `BinSwapped` events, detect when `token0BalanceScaled` (or `token1BalanceScaled`) reaches zero for a bin that still has shares, and call `addLiquidity` before the price reverses. No privileged access is required; `addLiquidity` is open to any caller with any `owner` address. [6](#0-5) 

### Recommendation

Add a guard in `LiquidityLib.addLiquidity` that rejects a deposit into a non-empty bin whose balance has been fully drained. One approach: if `binTotalSharesVal > 0` and both `binState.token0BalanceScaled == 0` and `binState.token1BalanceScaled == 0`, revert with a new `DrainedBin` error. Alternatively, require that `amount0Scaled > 0 || amount1Scaled > 0` whenever `binTotalSharesVal > 0` before writing any state, mirroring the invariant that shares must always represent a non-zero token claim.

### Proof of Concept

1. Pool is deployed with bins `[-1, 0, 1]`. Alice adds 10 000 shares to bin `1` (above current price), depositing token0.
2. A series of `!zeroForOne` swaps (token1 in, token0 out) fully consume bin `1`: `_binStates[1].token0BalanceScaled = 0`, `_binStates[1].token1BalanceScaled = 0`, but `_binTotalShares[1] = 10 000` (Alice's shares remain).
3. Bob calls `addLiquidity(owner=Bob, salt=0, deltas={binIdxs:[1], shares:[10_000]}, callbackData="", extensionData="")`.
   - `binTotalSharesVal = 10 000 > 0` → proportional branch taken.
   - `amount0Scaled = ceilDiv(0 * 10_000, 10_000) = 0`.
   - `amount1Scaled = ceilDiv(0 * 10_000, 10_000) = 0`.
   - `amount0Added = 0`, `amount1Added = 0` → callback skipped.
   - `_binTotalShares[1]` updated to `20 000`; `_positionBinShares[key(Bob,0,1)]` set to `10 000`. **No tokens paid.**
4. Price reverses; `zeroForOne` swaps refill bin `1` with 1 000 units of token0 (scaled).
5. Bob calls `removeLiquidity` for his 10 000 shares:
   - `amount0Scaled = 1 000 * 10_000 / 20_000 = 500` → Bob receives 500 units of token0.
   - Alice removes her 10 000 shares and receives only 500 units instead of 1 000.
   - Alice loses 500 units of token0 she legitimately deposited. [2](#0-1) [7](#0-6)

### Citations

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L85-111)
```text
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
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L112-121)
```text
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
