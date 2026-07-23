Looking at the codebase, I need to find a missing zero-address check analog in the Metric OMM core pool that can affect real token balances, LP claims, fees, or swap outputs.

**Key finding in `MetricOmmPool.addLiquidity`:**

The `addLiquidity` function accepts an `owner` parameter with no zero-address guard, while `removeLiquidity` enforces `msg.sender == owner` — making any position minted to `address(0)` permanently irremovable. [1](#0-0) [2](#0-1) 

The periphery does guard this, but the core pool does not: [3](#0-2) 

---

### Title
Missing Zero Address Check for `owner` in `addLiquidity()` Permanently Locks Deposited Tokens and LP Claims — (File: metric-core/contracts/MetricOmmPool.sol)

### Summary
`MetricOmmPool.addLiquidity` accepts an `owner` address with no zero-address guard. If `owner == address(0)` is supplied, the pool accepts real token deposits via callback and mints LP shares to `address(0)`, but the position can never be withdrawn because `removeLiquidity` enforces `msg.sender == owner` and `msg.sender` can never equal `address(0)`. Deposited tokens are permanently locked.

### Finding Description
`MetricOmmPool.addLiquidity` (line 182) forwards `owner` directly to `LiquidityLib.addLiquidity`, which keys per-bin position shares under `_positionBinShares[keccak256(abi.encode(owner, salt, binIdx))]`. No `require(owner != address(0))` guard exists anywhere in the core pool path.

When `owner == address(0)`:
1. The pool invokes `metricOmmModifyLiquidityCallback` on `msg.sender`, which transfers real tokens into the pool.
2. `binTotals.scaledToken0/1` and `_binStates[binIdx].token0/1BalanceScaled` are updated correctly — the pool's accounting reflects the deposit.
3. `_positionBinShares[keccak256(abi.encode(address(0), salt, binIdx))]` is set to the minted share amount.
4. `removeLiquidity` (line 206) enforces `if (msg.sender != owner) revert NotPositionOwner()`. Since `msg.sender` can never be `address(0)`, the position is permanently irremovable.

The periphery contract `MetricOmmPoolLiquidityAdder._validateOwner` (line 247–249) does check for zero address, but this guard exists only in the periphery. The core pool is a public `external` contract callable by any contract, and the protection is absent at the protocol layer. Any integrator building a custom periphery or calling the core pool directly can trigger this path.

### Impact Explanation
Any contract that calls `MetricOmmPool.addLiquidity` with `owner == address(0)` will deposit real token amounts (transferred via callback settlement) that are permanently locked. The LP shares are minted to `address(0)` and can never be redeemed via `removeLiquidity`. This is a direct, irreversible loss of user principal with no recovery path — the pool's `binTotals` correctly accounts for the tokens, but no address can ever claim them back.

### Likelihood Explanation
Low. The caller must pass `address(0)` as `owner`, which is a user or integrator error. However, the core pool is a public contract callable by any contract (not only the official periphery), and integrators building custom periphery contracts may omit the zero-address check. The periphery's guard does not protect direct callers of the core pool.

### Recommendation
Add a zero-address check at the top of `addLiquidity` in `MetricOmmPool.sol`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
+   if (owner == address(0)) revert InvalidPositionOwner();
    if (deltas.binIdxs.length == 0) return (0, 0);
    ...
}
```

### Proof of Concept
1. Deploy a contract `Attacker` that implements `metricOmmModifyLiquidityCallback` and transfers tokens to the pool on callback.
2. Call `MetricOmmPool.addLiquidity(address(0), salt, deltas, callbackData, "")` from `Attacker`.
3. The callback fires; tokens are transferred to the pool; `_positionBinShares[keccak256(abi.encode(address(0), salt, binIdx))]` is set to the minted share amount.
4. Attempt `MetricOmmPool.removeLiquidity(address(0), salt, deltas, "")` from any address — it reverts with `NotPositionOwner()` because `msg.sender != address(0)`.
5. The deposited tokens are permanently locked in the pool with no recovery path.

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
