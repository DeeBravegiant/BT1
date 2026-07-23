### Title
Missing `owner != address(0)` Validation in `addLiquidity` Permanently Locks LP Tokens - (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.addLiquidity` accepts `address(0)` as the `owner` argument without any guard. Because `removeLiquidity` enforces `msg.sender == owner`, and `msg.sender` can never equal `address(0)`, any tokens deposited under an `address(0)` owner are permanently locked inside the pool. The periphery helper `MetricOmmPoolLiquidityAdder` carries this check, but the core pool — the canonical entry point — does not.

---

### Finding Description

`MetricOmmPool.addLiquidity` (lines 182–196) performs two input checks before delegating to `LiquidityLib.addLiquidity`: it rejects an empty `binIdxs` array and a length mismatch between `binIdxs` and `shares`. There is no check that `owner != address(0)`.

```solidity
// MetricOmmPool.sol lines 182-196
function addLiquidity(
    address owner,          // ← never validated against address(0)
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    ...
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
        _liquidityContext(), owner, salt, deltas, callbackData,
        binTotals, _binStates, _binTotalShares, _positionBinShares
    );
}
```

Inside `LiquidityLib.addLiquidity` (lines 72, 120–121), the position key is computed and shares are credited:

```solidity
bytes32 posKey = _positionBinKey(owner, salt, int8(binIdx));   // owner == address(0)
...
binTotalShares[binIdx] = binTotalSharesVal + sharesToAdd;
positionBinShares[posKey] = newUserShares;
```

Tokens are then pulled from `msg.sender` via the modify-liquidity callback (line 147–148). The pool's `binState.token0BalanceScaled`, `binState.token1BalanceScaled`, and `binTotals.scaledToken0/1` are all incremented to reflect the deposit.

The withdrawal path (`removeLiquidity`, line 206) enforces:

```solidity
if (msg.sender != owner) revert NotPositionOwner();
```

Because `msg.sender` can never equal `address(0)`, the shares credited to `positionBinShares[keccak256(abi.encode(address(0), salt, bin))]` can never be burned. The deposited tokens are permanently locked.

The periphery contract `MetricOmmPoolLiquidityAdder._validateOwner` (lines 247–249) does carry this guard:

```solidity
function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
}
```

But the core pool is a standalone public contract; any EOA or contract can call it directly, bypassing the periphery entirely.

---

### Impact Explanation

**Direct loss of user principal.** Tokens transferred into the pool via the modify-liquidity callback are irrecoverable. The corrupted state persists across three storage locations:

| Storage slot | Effect |
|---|---|
| `positionBinShares[keccak256(address(0), salt, bin)]` | Permanently non-zero; shares can never be burned |
| `binTotalShares[binIdx]` | Permanently inflated; dilutes future share-to-token ratios for that bin |
| `binState.token0/1BalanceScaled` + `binTotals.scaledToken0/1` | Permanently inflated by the locked token amount |

The inflated `binTotalShares` means that subsequent LPs who add liquidity to the same bin pay proportionally more tokens per share (line 109–110 of `LiquidityLib`), because the denominator `binTotalSharesVal` includes the permanently locked shares. The locked tokens are never redistributed.

---

### Likelihood Explanation

**Low.** The periphery `MetricOmmPoolLiquidityAdder` blocks this path. Exploitation requires a caller to interact with the core pool directly — either through a custom integration that omits the zero-address check, or through a user/operator error. No privileged role is required; any address can call `addLiquidity` on the pool.

---

### Recommendation

Add a zero-address guard at the top of `MetricOmmPool.addLiquidity`, mirroring the check already present in the periphery:

```solidity
function addLiquidity(
    address owner,
    ...
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
    if (owner == address(0)) revert InvalidPositionOwner();   // ← add this
    if (deltas.binIdxs.length == 0) return (0, 0);
    ...
}
```

Alternatively, place the guard inside `LiquidityLib.addLiquidity` so it is enforced regardless of which entry point is used.

---

### Proof of Concept

```solidity
// Attacker (or mistaken integrator) implements IMetricOmmModifyLiquidityCallback
contract PoC is IMetricOmmModifyLiquidityCallback {
    IERC20 token0; IERC20 token1; IMetricOmmPool pool;

    function metricOmmModifyLiquidityCallback(
        uint256 a0, uint256 a1, bytes calldata
    ) external {
        if (a0 > 0) token0.transfer(msg.sender, a0);
        if (a1 > 0) token1.transfer(msg.sender, a1);
    }

    function attack() external {
        int256[] memory bins   = new int256[](1); bins[0]   = 0;
        uint256[] memory shares = new uint256[](1); shares[0] = 1_000_000;
        LiquidityDelta memory d = LiquidityDelta({binIdxs: bins, shares: shares});

        // owner == address(0): no revert, tokens pulled, shares locked forever
        pool.addLiquidity(address(0), 0, d, "", "");

        // Attempt to recover: reverts with NotPositionOwner because msg.sender != address(0)
        pool.removeLiquidity(address(0), 0, d, "");
    }
}
```

After `attack()`:
- `token0/1` transferred into the pool are unrecoverable.
- `binTotalShares[0]` is permanently inflated by `1_000_000`.
- `binState.token0BalanceScaled` and `binTotals.scaledToken0` are permanently inflated by the deposited scaled amount. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L70-121)
```text
          // safe because -128 <= LOWEST_BIN <= HIGHEST_BIN <= 127 (enforced by factory)
          // forge-lint: disable-next-line(unsafe-typecast)
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
