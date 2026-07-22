After thorough investigation of the codebase, I found a valid analog.

---

### Title
`uint32` Overflow in `_afterTimelock` Allows Pool Admin to Bypass Stop-Loss Timelock Immediately — (File: `metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

### Summary

`OracleValueStopLossExtension._afterTimelock` computes `uint32(block.timestamp + timelock)`. When `timelock` is set to any value greater than `type(uint32).max − block.timestamp` (currently ~2.54 billion seconds, ≈80 years), the addition overflows the `uint32` cast and produces an `executeAfter` that is already in the past. The downstream `_requireElapsed` check then passes immediately, letting the pool admin execute drawdown/decay/watermark changes with zero waiting time — defeating the entire LP-protection guarantee the timelock is meant to enforce.

### Finding Description

`_afterTimelock` returns a `uint32`:

```solidity
// OracleValueStopLossExtension.sol line 297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

`block.timestamp` is `uint256`; `timelock` is `uint32`. The addition is performed in `uint256` space, then truncated to `uint32`. If the sum exceeds `type(uint32).max` (4 294 967 295), the truncation wraps the result to a value smaller than `block.timestamp`.

`_requireElapsed` then compares `block.timestamp` (uint256) against the wrapped `uint32 executeAfter` (implicitly widened to uint256):

```solidity
// OracleValueStopLossExtension.sol line 301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(executeAfter, block.timestamp);
}
```

If `executeAfter` wrapped to, say, `block.timestamp − 1`, the condition `block.timestamp < (block.timestamp − 1)` is false, so the revert is never triggered and execution proceeds immediately.

The `PoolStopLossSchedule` stores all pending `executeAfter` values as `uint32`:

```solidity
// IOracleValueStopLossExtension.sol lines 20-27
struct PoolStopLossSchedule {
    uint32 pendingTimelock;
    uint32 pendingTimelockExecuteAfter;
    uint32 pendingDrawdownE6;
    uint32 pendingDrawdownExecuteAfter;
    uint32 pendingDecayPerSecondE8;
    uint32 pendingDecayExecuteAfter;
}
```

No upper-bound validation is applied to `timelock` at initialization or during updates:

```solidity
// OracleValueStopLossExtension.sol lines 56-62
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);
_validateDecay(decayPerSecondE8);
// ← no _validateTimelock
```

### Impact Explanation

The `OracleValueStopLossExtension` is the sole on-chain mechanism protecting LP principal from sudden drawdown. Its timelocked parameters are:

- `drawdownE6` — the maximum tolerated per-share value loss before swaps are blocked
- `decayPerSecondE8` — the rate at which watermarks decay (faster decay = weaker protection)
- per-bin high watermarks — the reference levels against which drawdown is measured

By bypassing the timelock, the pool admin can:
1. Set `drawdownE6 = 0` → stop-loss check becomes a no-op (`if (drawdown == 0) return;`), removing all LP protection.
2. Set `decayPerSecondE8 = type(uint32).max` → watermarks decay to zero within one second, making the stop-loss permanently inactive.
3. Overwrite watermarks to arbitrarily high values → the current metric can never breach the floor.

Any of these allows the pool to drain LP token balances through adversarial swaps without the extension reverting.

### Likelihood Explanation

The attack requires the pool admin to set `timelock` to a value exceeding `type(uint32).max − block.timestamp` (≈2 541 967 295 seconds at the time of writing). This is a valid `uint32` value (max is ≈4.29 billion). The admin can frame this as "setting a very long timelock for LP safety," which may not raise immediate suspicion. The actual current timelock must first elapse before the new (overflowing) timelock takes effect, but once set, all subsequent parameter changes bypass the delay entirely. The attack is therefore reachable by a semi-trusted pool admin and constitutes an admin-boundary break explicitly listed in the allowed impact gate.

### Recommendation

Replace `uint32` with `uint256` for all `executeAfter` storage fields and the return type of `_afterTimelock`, matching the pattern used in `MetricOmmPoolFactory.proposePoolPriceProvider` (which correctly uses `uint256 executeAfter = block.timestamp + timelock`). Alternatively, add an explicit upper-bound check on `timelock` during initialization and updates (e.g., `require(timelock <= 365 days)`).

### Proof of Concept

```
block.timestamp = 1_753_000_000   (≈ July 2026)
timelock        = 4_294_967_295   (type(uint32).max, set by admin)

_afterTimelock:
  uint256 sum = 1_753_000_000 + 4_294_967_295 = 6_047_967_295
  uint32(6_047_967_295) = 6_047_967_295 % 4_294_967_296 = 1_752_999_999

_requireElapsed(1_752_999_999):
  block.timestamp < executeAfter
  1_753_000_000  < 1_752_999_999  → false  → no revert → immediate execution
```

Step-by-step exploit:
1. Pool deployed with `timelock = 3 days` and a meaningful `drawdownE6`.
2. LPs add liquidity trusting the 3-day protection window.
3. Admin calls `proposeOracleStopLossTimelock(pool, type(uint32).max)`.
4. After 3 days, admin calls `executeOracleStopLossTimelock(pool)` → `timelock` is now `type(uint32).max`.
5. Admin calls `proposeOracleStopLossDrawdown(pool, 0)` → `executeAfter` wraps to `block.timestamp − 1`.
6. Admin immediately calls `executeOracleStopLossDrawdown(pool)` → passes, `drawdownE6 = 0`.
7. Stop-loss is silently disabled; subsequent swaps drain LP bins without triggering `OracleStopLossTriggered`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L56-62)
```text
    (uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
    _validateDrawdown(drawdownE6);
    _validateDecay(decayPerSecondE8);

    oracleStopLossConfig[pool] = PoolStopLossConfig({
      drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
    });
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L103-119)
```text
  function proposeOracleStopLossDrawdown(address pool_, uint256 newMaxDrawdownE6) external onlyPoolAdmin(pool_) {
    _validateDrawdown(newMaxDrawdownE6);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
    sched.pendingDrawdownExecuteAfter = executeAfter;
    emit OracleStopLossDrawdownProposed(pool_, newMaxDrawdownE6, executeAfter);
  }

  function executeOracleStopLossDrawdown(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingDrawdownExecuteAfter == 0) revert OracleStopLossNoPendingDrawdown(pool_);
    _requireElapsed(sched.pendingDrawdownExecuteAfter);
    uint32 drawdown = sched.pendingDrawdownE6;
    oracleStopLossConfig[pool_].drawdownE6 = drawdown;
    (sched.pendingDrawdownE6, sched.pendingDrawdownExecuteAfter) = (0, 0);
    emit OracleStopLossDrawdownSet(pool_, drawdown);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L215-217)
```text
    PoolStopLossConfig memory cfg = oracleStopLossConfig[pool_];
    uint256 drawdown = cfg.drawdownE6;
    if (drawdown == 0) return;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L297-299)
```text
  function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L301-303)
```text
  function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(executeAfter, block.timestamp);
  }
```

**File:** metric-periphery/contracts/interfaces/extensions/IOracleValueStopLossExtension.sol (L20-27)
```text
  struct PoolStopLossSchedule {
    uint32 pendingTimelock;
    uint32 pendingTimelockExecuteAfter;
    uint32 pendingDrawdownE6;
    uint32 pendingDrawdownExecuteAfter;
    uint32 pendingDecayPerSecondE8;
    uint32 pendingDecayExecuteAfter;
  }
```
