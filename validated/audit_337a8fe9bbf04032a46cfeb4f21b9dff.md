### Title
LP Funds Permanently Stuck When `addLiquidity` Operator Sets `owner` to an Address Incapable of Calling `removeLiquidity` — No Rescue Mechanism Exists — (File: metric-core/contracts/MetricOmmPool.sol)

---

### Summary

`addLiquidity` explicitly supports an operator pattern where `msg.sender` pays but any arbitrary `owner` address receives the position. `removeLiquidity` enforces a hard `msg.sender == owner` check with no delegation, no position-transfer mechanism, and no factory-level rescue path for pool-held LP tokens. If `owner` is set to a contract that cannot itself call `removeLiquidity` (e.g., a smart-contract wallet lacking the interface, a bricked multisig, or an address supplied by mistake), the deposited tokens are permanently locked in the pool.

---

### Finding Description

`addLiquidity` accepts an arbitrary `owner` parameter and records the position under the key `keccak256(abi.encode(owner, salt, bin))`. The paying caller (`msg.sender`) need not equal `owner`; this is the documented "operator pattern." [1](#0-0) 

`removeLiquidity` enforces a strict identity check before any state is touched: [2](#0-1) 

Tokens are then transferred directly to `owner` inside `LiquidityLib.removeLiquidity`: [3](#0-2) 

There is no position-transfer function, no operator-approved-withdrawal path, and no factory rescue for pool-held LP balances. The factory's `collectTokens` rescues tokens held by the factory contract itself, not by individual pools: [4](#0-3) 

The periphery `addLiquidityExactShares(pool, owner, …)` overload validates only that `owner != address(0)` before forwarding the arbitrary address to the core pool: [5](#0-4) 

---

### Impact Explanation

Any tokens deposited into a position whose `owner` cannot execute `removeLiquidity` are permanently irrecoverable. The pool holds the real ERC-20 balances; `binTotals` and per-bin `token(0|1)BalanceScaled` correctly account for them, but no code path exists to release them without `msg.sender == owner`. The loss is unbounded — an operator can deposit any amount on behalf of an inaccessible address.

---

### Likelihood Explanation

The operator pattern is a first-class, documented feature used by `MetricOmmPoolLiquidityAdder`. Real-world triggers include:

- A smart-contract wallet or vault that calls `addLiquidityExactShares(pool, vaultAddress, …)` but does not implement a `removeLiquidity` forwarding function.
- A multisig set as `owner` that later loses quorum or is upgraded to a new address.
- An off-by-one or copy-paste error supplying the wrong `owner` address in an integration script.

None of these require malicious intent; all are plausible production mistakes.

---

### Recommendation

1. **Add a position-transfer function** (analogous to ERC-721 `transferFrom`) so `owner` can delegate withdrawal rights to a new address.
2. **Alternatively, add a factory-callable rescue path** that allows the pool admin to release funds from a position whose `owner` can be proven inaccessible (e.g., via a timelock + proof-of-no-activity mechanism), mirroring the `rescueFunds` recommendation from the Pip.sol report.
3. At minimum, **document the irrecoverability risk** prominently in `addLiquidity` NatSpec so integrators understand that setting `owner` to a contract requires that contract to be able to call `removeLiquidity`.

---

### Proof of Concept

```
1. Alice (operator) calls:
   pool.addLiquidity(
       owner  = address(vaultContract),   // vault has no removeLiquidity path
       salt   = 0,
       deltas = {binIdxs: [4], shares: [100_000]},
       ...
   )
   → Alice pays token0; pool records position under keccak256(vaultContract, 0, 4).
   → binState.token0BalanceScaled += amount0Scaled; binTotals.scaledToken0 += amount0Scaled.

2. Alice (or anyone) later calls:
   pool.removeLiquidity(address(vaultContract), 0, deltas, "")
   → Line 206: msg.sender (Alice) != owner (vaultContract) → revert NotPositionOwner()

3. vaultContract itself cannot call removeLiquidity because it has no such function.

4. No factory rescue path exists for pool-held LP balances.

Result: token0 deposited in step 1 is permanently locked in the pool.
``` [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-148)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
  /// @param owner Position owner encoded in the pool’s position key.
```

**File:** metric-core/contracts/MetricOmmPool.sol (L182-212)
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

  /// @inheritdoc IMetricOmmPoolActions
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L108-121)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-67)
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
```
