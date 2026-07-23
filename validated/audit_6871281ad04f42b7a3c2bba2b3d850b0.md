### Title
Missing `whenNotPaused` Modifier on `addLiquidity` and `removeLiquidity` Allows Liquidity Operations on a Paused Pool - (File: metric-core/contracts/MetricOmmPool.sol)

### Summary

`MetricOmmPool` defines a `whenNotPaused` modifier backed by `_checkNotPaused()`, and correctly applies it to `swap`. However, `addLiquidity` and `removeLiquidity` are missing this modifier entirely, allowing both functions to execute against pool state even when `pauseLevel != 0`.

### Finding Description

`MetricOmmPool` exposes two modifiers relevant to pool-wide safety: [1](#0-0) 

`swap` correctly carries both guards: [2](#0-1) 

But `addLiquidity` and `removeLiquidity` carry only `nonReentrant`, with no pause check: [3](#0-2) [4](#0-3) 

The pause check itself is: [5](#0-4) 

`pauseLevel` can be set to 1 (admin pause) or 2 (protocol pause) via `setPause`, which is an `onlyFactory` call: [6](#0-5) 

### Impact Explanation

When the pool is paused — typically in response to an oracle failure, an exploit, or a migration — the intended invariant is that no state-mutating pool operations proceed. Because `addLiquidity` and `removeLiquidity` bypass the pause check:

1. **`addLiquidity` while paused**: An LP (or an attacker front-running a pause) can deposit tokens into a pool whose oracle or bin state is known to be compromised. `LiquidityLib.addLiquidity` reads `curBinIdx` and `curPosInBin` from live storage and mints shares against those values. If the pool was paused precisely because those values are stale or corrupted, the LP's deposited tokens are immediately at risk the moment the pool is unpaused and swaps resume at the bad price.

2. **`removeLiquidity` while paused**: A protocol-level pause (level 2) may be intended to freeze all operations — e.g., during an emergency upgrade or insolvency investigation. LPs can still drain their positions, potentially racing ahead of a recovery action and leaving the pool insolvent for remaining participants.

Both paths move real token balances (`safeTransfer` in/out) and mutate `binTotals`, `_binTotalShares`, and `_positionBinShares` while the pool is supposed to be frozen.

### Likelihood Explanation

The pool admin or protocol can pause at any time. Any user who monitors the mempool for a `setPause` transaction can front-run it with `addLiquidity` or `removeLiquidity`. Even without front-running, any user can call these functions in the same block as or after a pause is set, because the pause check is simply absent.

### Recommendation

Add `whenNotPaused` to both functions, mirroring the pattern already used by `swap`:

```solidity
function addLiquidity(...) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) ...

function removeLiquidity(...) external whenNotPaused nonReentrant(PoolActions.REMOVE_LIQUIDITY) ...
```

If emergency LP withdrawal during a pause is a deliberate design choice, document it explicitly and restrict it to `pauseLevel == 1` (admin pause only), while blocking both operations at `pauseLevel == 2` (protocol pause).

### Proof of Concept

```solidity
// 1. Factory pauses the pool (e.g., oracle compromise detected)
factory.setPause(address(pool), 2);

// 2. Pool is "paused" — swap correctly reverts:
pool.swap(...); // reverts PoolPaused ✓

// 3. But addLiquidity proceeds without revert:
pool.addLiquidity(attacker, salt, deltas, callbackData, ""); // succeeds ✗

// 4. removeLiquidity also proceeds:
pool.removeLiquidity(lp, salt, deltas, ""); // succeeds ✗

// 5. When pool is unpaused, attacker's shares are backed by
//    tokens deposited at a compromised bin position, and
//    swaps drain honest LPs.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L168-177)
```text
  // ============ Modifiers ============
  modifier onlyFactory() {
    _checkFactory();
    _;
  }

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

**File:** metric-core/contracts/MetricOmmPool.sol (L198-212)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L455-461)
```text
  function setPause(uint8 newLevel) external onlyFactory {
    if (newLevel > 2) revert InvalidPauseLevel();
    if (newLevel == pauseLevel) return;
    uint8 prev = pauseLevel;
    pauseLevel = newLevel;
    emit PauseLevelUpdated(prev, newLevel);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L643-645)
```text
  function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
  }
```
