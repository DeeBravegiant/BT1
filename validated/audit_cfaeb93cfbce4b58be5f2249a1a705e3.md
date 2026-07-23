### Title
`addLiquidity` and `removeLiquidity` Bypass Emergency Pause, Allowing Token Movements When Pool Is Paused — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool` implements a three-level pause system (`pauseLevel` 0/1/2) and exposes a `whenNotPaused` modifier, but only applies it to `swap`. The `addLiquidity` and `removeLiquidity` functions are missing the `whenNotPaused` guard, so they continue to transfer real token balances in and out of the pool regardless of pause state.

---

### Finding Description

`MetricOmmPool` defines a `whenNotPaused` modifier that reverts when `pauseLevel != 0`: [1](#0-0) [2](#0-1) 

`swap` correctly carries this modifier: [3](#0-2) 

But `addLiquidity` and `removeLiquidity` do not: [4](#0-3) [5](#0-4) 

Both functions delegate to `LiquidityLib`, which performs real `safeTransfer`/`safeTransferFrom` calls on `TOKEN0` and `TOKEN1`, updating `binTotals`, `_binTotalShares`, and `_positionBinShares`. These are not read-only operations — they move actual token balances and mutate pool accounting state.

The factory's pause transitions are:
- `pausePool` (pool admin): 0 → 1
- `protocolPausePool` (protocol owner): 0 or 1 → 2
- `unpausePool` (pool admin): 1 → 0
- `protocolUnpausePool` (protocol owner): 2 → 1 (not to 0 — protocol cannot fully unpause) [6](#0-5) [7](#0-6) 

This design shows the pause is intended as a meaningful emergency stop. However, because `addLiquidity` and `removeLiquidity` ignore `pauseLevel`, the emergency mechanism is incomplete.

---

### Impact Explanation

When the pool is paused at level 1 or 2:

- Any LP can call `removeLiquidity` to withdraw their share of `TOKEN0`/`TOKEN1` from the pool, draining real token balances.
- Any caller can call `addLiquidity` to inject tokens and mint shares, mutating `binTotals` and share accounting while the pool is in an undefined/emergency state.
- An attacker who triggered the emergency can continue extracting funds via `removeLiquidity` even after the admin activates the pause, defeating the entire purpose of the emergency mechanism.
- Pool solvency invariant (`balance >= binTotals + fees`) can be broken during a paused state if concurrent liquidity removals race with the pause activation.

This is a **direct loss of user principal and protocol LP assets** — `removeLiquidity` transfers tokens out of the pool unconditionally.

---

### Likelihood Explanation

- The trigger requires only that the pool be paused (a normal admin or protocol action) and that an LP (or attacker with a position) calls `removeLiquidity`. No special privilege is needed beyond holding a position.
- The scenario where this matters most — an active exploit prompting an emergency pause — is exactly when an attacker would race to call `removeLiquidity` before the pause takes effect, and then continue calling it after.
- `addLiquidity` is callable by anyone (no `msg.sender == owner` check), making it reachable by any unprivileged actor.

---

### Recommendation

Apply `whenNotPaused` to both `addLiquidity` and `removeLiquidity`, consistent with how it is applied to `swap`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) { ... }

function removeLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.REMOVE_LIQUIDITY) returns (...) { ... }
```

---

### Proof of Concept

1. Pool admin or protocol owner calls `pausePool(pool)` or `protocolPausePool(pool)` → `pauseLevel` becomes 1 or 2.
2. `swap(...)` now reverts with `PoolPaused` due to `whenNotPaused`.
3. Attacker (or any LP) calls `removeLiquidity(owner, salt, deltas, "")` directly on the pool.
4. `removeLiquidity` has no `whenNotPaused` check — it proceeds through `LiquidityLib.removeLiquidity`, decrements `_binTotalShares` and `_positionBinShares`, reduces `binTotals.scaledToken0`/`scaledToken1`, and calls `safeTransfer` to send real tokens to `owner`.
5. The pause is fully bypassed; tokens leave the pool while it is in an emergency-paused state. [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L174-177)
```text
  modifier whenNotPaused() {
    _checkNotPaused();
    _;
  }
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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

**File:** metric-core/contracts/MetricOmmPool.sol (L643-645)
```text
  function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L392-403)
```text
  function protocolPausePool(address pool) external override nonReentrant onlyOwner {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 0 && cur != 1) revert InvalidPauseTransition(cur, 2);
    IMetricOmmPoolFactoryActions(pool).setPause(2);
  }

  /// @inheritdoc IMetricOmmPoolFactoryOwner
  function protocolUnpausePool(address pool) external override nonReentrant onlyOwner {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 2) revert InvalidPauseTransition(cur, 1);
    IMetricOmmPoolFactoryActions(pool).setPause(1);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L460-471)
```text
  function pausePool(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 0) revert InvalidPauseTransition(cur, 1);
    IMetricOmmPoolFactoryActions(pool).setPause(1);
  }

  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
  function unpausePool(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 1) revert InvalidPauseTransition(cur, 0);
    IMetricOmmPoolFactoryActions(pool).setPause(0);
  }
```
