### Title
LP Principal Permanently Locked When `addLiquidity` Is Called With `owner = address(0)` — (`metric-core/contracts/libraries/LiquidityLib.sol`)

---

### Summary

`addLiquidity` accepts `owner = address(0)` without any zero-address guard. Shares are minted to the position key `keccak256(abi.encode(address(0), salt, bin))`, tokens are pulled from `msg.sender` into the pool, and the position becomes permanently irrecoverable because `removeLiquidity` enforces `msg.sender == owner`, which can never be satisfied when `owner = address(0)`.

---

### Finding Description

`MetricOmmPool.addLiquidity` passes the caller-supplied `owner` directly into `LiquidityLib.addLiquidity` with no zero-address validation: [1](#0-0) 

Inside `LiquidityLib.addLiquidity`, `owner` is used verbatim to derive the position key and credit shares: [2](#0-1) 

Tokens are then pulled from `msg.sender` via the modify-liquidity callback and credited to the pool's `binTotals` and per-bin balances: [3](#0-2) 

The only path to recover those tokens is `removeLiquidity`, which hard-requires `msg.sender == owner`: [4](#0-3) 

Because `msg.sender` is never `address(0)` in a valid EVM transaction, the position key `keccak256(abi.encode(address(0), salt, bin))` is permanently unclaimable. The deposited tokens remain in the pool's accounting (`binState.token0BalanceScaled` / `token1BalanceScaled` and `binTotals`) but can never be withdrawn. [5](#0-4) 

---

### Impact Explanation

The caller's deposited token0 and token1 are permanently locked inside the pool. The pool's `binTotals` and per-bin balances are inflated by the locked amounts, but no address can ever call `removeLiquidity` to reclaim them. This is a direct, irreversible loss of user principal with no recovery path.

---

### Likelihood Explanation

Any unprivileged caller can trigger this by passing `owner = address(0)` to `addLiquidity`. It requires no special role, no malicious token, and no privileged setup. It can occur accidentally (e.g., a misconfigured integration that zero-initialises the owner field) or deliberately as a griefing vector. The `msg.sender` pays the tokens and loses them.

---

### Recommendation

Add a zero-address guard at the top of `addLiquidity` in `MetricOmmPool.sol` (or equivalently at the entry of `LiquidityLib.addLiquidity`):

```solidity
require(owner != address(0), "addLiquidity: owner is zero address");
```

---

### Proof of Concept

1. Attacker (or misconfigured integrator) calls:
   ```solidity
   pool.addLiquidity(
       address(0),   // owner = zero address
       0,            // salt
       deltas,       // valid bin/share arrays
       callbackData,
       extensionData
   );
   ```
2. `LiquidityLib.addLiquidity` computes `posKey = keccak256(abi.encode(address(0), 0, binIdx))` and credits shares there.
3. The callback fires; `msg.sender` transfers `amount0Added` and `amount1Added` into the pool.
4. `binTotals.scaledToken0` / `scaledToken1` and the per-bin balances are incremented.
5. Any subsequent call to `removeLiquidity(address(0), 0, deltas, ...)` reverts at `if (msg.sender != owner) revert NotPositionOwner()` because `msg.sender` cannot be `address(0)`.
6. The deposited tokens are permanently locked.

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L242-247)
```text
      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
      if (amount1Removed > 0) {
        IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
      }
```
