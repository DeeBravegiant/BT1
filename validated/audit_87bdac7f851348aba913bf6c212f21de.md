### Title
Smart-contract LP owner permanently locked out of `removeLiquidity` — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`removeLiquidity` enforces `msg.sender == owner` as a hard gate. `addLiquidity` deliberately supports an operator pattern where `msg.sender` may differ from `owner`. The asymmetry means any smart-contract `owner` that cannot itself call `removeLiquidity` (e.g., a vault, multisig, DAO, or any contract without a `removeLiquidity` dispatch path) has its LP principal permanently locked in the pool.

---

### Finding Description

`MetricOmmPool.removeLiquidity` contains the following check:

```solidity
if (msg.sender != owner) revert NotPositionOwner();
``` [1](#0-0) 

`addLiquidity` has no symmetric restriction — `msg.sender` pays via callback while `owner` is a freely chosen address:

```solidity
function addLiquidity(
    address owner,   // ← may differ from msg.sender
    uint80 salt,
    ...
``` [2](#0-1) 

The periphery `MetricOmmPoolLiquidityAdder` explicitly documents and exercises this operator pattern — `msg.sender` funds the deposit while `owner` is recorded as the position holder:

```solidity
/// @dev `msg.sender` is always the payer for token pulls in callback
///      (stored in transient settlement context).
function addLiquidityExactShares(
    address pool,
    address owner,   // ← distinct from msg.sender
    ...
``` [3](#0-2) 

When `removeLiquidity` is called, `LiquidityLib.removeLiquidity` transfers tokens directly to `owner`:

```solidity
if (amount0Removed > 0) {
    IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
}
if (amount1Removed > 0) {
    IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
}
``` [4](#0-3) 

Because `removeLiquidity` requires `msg.sender == owner`, a smart-contract `owner` that has no internal function to call `removeLiquidity` on the pool can never withdraw. There is no alternative withdrawal path, no operator delegation, and no factory escape hatch.

---

### Impact Explanation

LP principal (token0 and token1) deposited under a smart-contract `owner` address is permanently irrecoverable if that contract cannot itself dispatch `removeLiquidity`. This is a direct loss of user funds — the tokens remain in the pool, credited to a position that can never be burned. The pool's solvency invariant is not broken (the pool holds the tokens), but the LP's claim on those tokens is permanently unexercisable.

---

### Likelihood Explanation

The operator pattern in `addLiquidity` is a first-class, documented feature of both the core pool and the periphery `MetricOmmPoolLiquidityAdder`. Any integrator that:

1. Uses `addLiquidityExactShares(pool, owner, ...)` or `addLiquidityWeighted(pool, owner, ...)` with `owner` set to a contract address (vault, multisig, DAO treasury, yield aggregator), **and**
2. That contract does not implement a function that calls `pool.removeLiquidity(address(this), ...)`,

will have its LP position permanently locked. This is a realistic and common integration pattern.

---

### Recommendation

Mirror the operator pattern from `addLiquidity` in `removeLiquidity`. Allow an approved operator (or any caller) to call `removeLiquidity` on behalf of `owner`, provided tokens are still sent to `owner`. The simplest fix is to remove the `msg.sender == owner` restriction and always transfer proceeds to the `owner` argument:

```solidity
// Remove this line:
if (msg.sender != owner) revert NotPositionOwner();
```

Tokens are already sent to `owner` by `LiquidityLib.removeLiquidity`, so the `owner` address is never at risk of being redirected. If stricter access control is desired, introduce an explicit operator approval mapping (`approvedOperators[owner][msg.sender]`) rather than the identity check.

---

### Proof of Concept

1. Deploy a smart contract `Vault` that calls `addLiquidityExactShares(pool, address(this), salt, deltas, ...)` — this records `address(Vault)` as the position `owner`.
2. `Vault` does not implement any function that calls `pool.removeLiquidity(address(this), ...)`.
3. Any attempt to call `pool.removeLiquidity(address(Vault), salt, deltas, ...)` from any EOA or other contract reverts with `NotPositionOwner` because `msg.sender != address(Vault)`.
4. The LP shares and underlying token0/token1 are permanently locked in the pool under the `Vault` position key.

The position key is `keccak256(abi.encode(owner, salt, bin))`: [5](#0-4) 

No path exists to burn shares for this key without `msg.sender == address(Vault)`.

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L52-68)
```text
  /// @dev `msg.sender` is always the payer for token pulls in callback (stored in transient settlement context).
  /// @param owner Position owner recorded by the pool.
  /// @param maxAmountToken0 Max token0 (native units) the pool may request; inclusive check before pull.
  /// @param maxAmountToken1 Max token1 (native units) the pool may request; inclusive check before pull.
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L242-247)
```text
      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
      if (amount1Removed > 0) {
        IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
      }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L256-259)
```text
  function _positionBinKey(address owner, uint80 salt, int8 bin) internal pure returns (bytes32 key) {
    // forge-lint: disable-next-line(asm-keccak256)
    return keccak256(abi.encode(owner, salt, bin));
  }
```
