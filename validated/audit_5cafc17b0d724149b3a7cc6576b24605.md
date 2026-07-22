### Title
OracleValueStopLossExtension Timelock Fully Bypassed After Admin Reduces It to Zero — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension` uses a single shared timelock value (`oracleStopLossConfig[pool_].timelock`) to gate all parameter changes (drawdown, decay, high watermarks). Because the timelock itself is also changeable via the same timelocked flow, a pool admin can first reduce the timelock to `0` (paying the original delay once), and then bypass all future timelocks for every other parameter — including disabling the stop-loss entirely — in the same block.

---

### Finding Description

Every `propose*` function computes the execution deadline via `_afterTimelock`:

```solidity
// OracleValueStopLossExtension.sol L297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [1](#0-0) 

And `_requireElapsed` enforces it:

```solidity
// L301-303
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
``` [2](#0-1) 

`proposeOracleStopLossTimelock` accepts any `uint32 newTimelock`, including `0`, with no lower-bound validation:

```solidity
// L78-84
function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);   // uses current (non-zero) timelock
    sched.pendingTimelock = newTimelock;            // newTimelock == 0 accepted
    sched.pendingTimelockExecuteAfter = executeAfter;
    ...
}
``` [3](#0-2) 

After `executeOracleStopLossTimelock` commits `timelock = 0`:

```solidity
// L86-94
function executeOracleStopLossTimelock(address pool_) external onlyPoolAdmin(pool_) {
    ...
    oracleStopLossConfig[pool_].timelock = timelock;   // now 0
    ...
}
``` [4](#0-3) 

Every subsequent `propose*` call now sets `pendingXxxExecuteAfter = block.timestamp + 0 = block.timestamp`. The corresponding `execute*` call checks `block.timestamp < block.timestamp` → `false`, so it never reverts. The pool admin can propose and execute any parameter change atomically in the same block, or across any two consecutive blocks, with zero delay.

This affects all three timelocked parameter families:

- `proposeOracleStopLossDrawdown` / `executeOracleStopLossDrawdown` (L103–120)
- `proposeOracleStopLossDecay` / `executeOracleStopLossDecay` (L130–147)
- `proposeOracleStopLossHighWatermarks` / `executeOracleStopLossHighWatermarks` (L157–177) [5](#0-4) [6](#0-5) [7](#0-6) 

---

### Impact Explanation

The stop-loss extension is the primary LP protection mechanism in pools that use it. Its `afterSwap` hook reverts swaps that would push per-share bin value below the drawdown floor:

```solidity
// L216-217
uint256 drawdown = cfg.drawdownE6;
if (drawdown == 0) return;   // stop-loss entirely disabled when drawdown == 0
``` [8](#0-7) 

After the bypass, the pool admin can:

1. **Disable stop-loss entirely** by setting `drawdownE6 = 0` — the `afterSwap` hook becomes a no-op, removing all LP value protection.
2. **Neutralise the stop-loss** by setting `drawdownE6 = 1e6` (100%) — the floor becomes 0, so the check `metric < (hwm * 0) / E6 = 0` is never true.
3. **Accelerate watermark decay** by setting `decayPerSecondE8` to maximum — watermarks collapse to 0 within seconds, making the stop-loss permanently inactive.
4. **Manipulate high watermarks** to artificially trigger or suppress stop-loss on specific bins.

All of these can be executed without LPs receiving the warning period the timelock was designed to provide, directly breaking the LP protection invariant.

---

### Likelihood Explanation

The pool admin is a semi-trusted role that LPs explicitly accept when depositing into a pool with this extension. The timelock is the only mechanism that gives LPs time to exit before adverse parameter changes take effect. The bypass requires only two transactions separated by the original timelock duration — a one-time cost. After that, the admin has permanent instant control over all stop-loss parameters. Any pool admin who is malicious or compromised can execute this.

---

### Recommendation

Add a minimum timelock floor that cannot be reduced below a protocol-defined constant (e.g., `MIN_TIMELOCK = 1 days`), and enforce it in `proposeOracleStopLossTimelock`:

```solidity
uint256 private constant MIN_TIMELOCK = 1 days;

function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    if (newTimelock < MIN_TIMELOCK) revert TimelockBelowMinimum(newTimelock, MIN_TIMELOCK);
    ...
}
```

Alternatively, use the **new** proposed timelock (not the current one) to gate the timelock-change itself, so reducing the timelock always requires waiting the longer of the two values.

---

### Proof of Concept

```
Setup:
  Pool created with OracleValueStopLossExtension, timelock = 7 days, drawdownE6 = 50_000 (5%)

Step 1 (t=0):
  poolAdmin calls proposeOracleStopLossTimelock(pool, 0)
  → pendingTimelock = 0
  → pendingTimelockExecuteAfter = block.timestamp + 7 days

Step 2 (t = 7 days + 1):
  poolAdmin calls executeOracleStopLossTimelock(pool)
  → oracleStopLossConfig[pool].timelock = 0   ✓ (waited once)

Step 3 (t = 7 days + 2, same block):
  poolAdmin calls proposeOracleStopLossDrawdown(pool, 0)
  → _afterTimelock returns block.timestamp + 0 = block.timestamp
  → pendingDrawdownExecuteAfter = block.timestamp

Step 4 (same block or any later block):
  poolAdmin calls executeOracleStopLossDrawdown(pool)
  → _requireElapsed(block.timestamp): block.timestamp < block.timestamp → false → no revert
  → oracleStopLossConfig[pool].drawdownE6 = 0

Result:
  afterSwap now hits `if (drawdown == 0) return` and exits immediately.
  Stop-loss is permanently disabled. LPs received zero warning.
  Steps 3–4 can be repeated for decay and high watermarks with the same zero-delay bypass.
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L78-84)
```text
  function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    emit OracleStopLossTimelockProposed(pool_, newTimelock, executeAfter);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L86-94)
```text
  function executeOracleStopLossTimelock(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingTimelockExecuteAfter == 0) revert OracleStopLossNoPendingTimelock(pool_);
    _requireElapsed(sched.pendingTimelockExecuteAfter);
    uint32 timelock = sched.pendingTimelock;
    oracleStopLossConfig[pool_].timelock = timelock;
    (sched.pendingTimelock, sched.pendingTimelockExecuteAfter) = (0, 0);
    emit OracleStopLossTimelockSet(pool_, timelock);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L103-120)
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
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L130-147)
```text
  function proposeOracleStopLossDecay(address pool_, uint256 newDecayPerSecondE8) external onlyPoolAdmin(pool_) {
    _validateDecay(newDecayPerSecondE8);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDecayPerSecondE8 = uint32(newDecayPerSecondE8);
    sched.pendingDecayExecuteAfter = executeAfter;
    emit OracleStopLossDecayProposed(pool_, newDecayPerSecondE8, executeAfter);
  }

  function executeOracleStopLossDecay(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingDecayExecuteAfter == 0) revert OracleStopLossNoPendingDecay(pool_);
    _requireElapsed(sched.pendingDecayExecuteAfter);
    uint32 decay = sched.pendingDecayPerSecondE8;
    oracleStopLossConfig[pool_].decayPerSecondE8 = decay;
    (sched.pendingDecayPerSecondE8, sched.pendingDecayExecuteAfter) = (0, 0);
    emit OracleStopLossDecaySet(pool_, decay);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L157-177)
```text
  function proposeOracleStopLossHighWatermarks(address pool_, int8 binIdx, uint104 newHwmToken0, uint104 newHwmToken1)
    external
    onlyPoolAdmin(pool_)
  {
    _requireInitialized(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    pendingHighWatermark[pool_] =
      PendingHighWatermarks({token0: newHwmToken0, token1: newHwmToken1, binIdx: binIdx, executeAfter: executeAfter});
    emit OracleStopLossHighWatermarkProposed(pool_, binIdx, newHwmToken0, newHwmToken1, executeAfter);
  }

  /// @notice Apply the pending watermarks. Also resets the decay clock for the bin.
  function executeOracleStopLossHighWatermarks(address pool_) external onlyPoolAdmin(pool_) {
    PendingHighWatermarks memory pending = pendingHighWatermark[pool_];
    if (pending.executeAfter == 0) revert OracleStopLossNoPendingHighWatermark(pool_);
    _requireElapsed(pending.executeAfter);
    highWatermarks[pool_][pending.binIdx] =
      BinHighWatermarks({token0: pending.token0, token1: pending.token1, lastDecayTs: uint32(block.timestamp)});
    delete pendingHighWatermark[pool_];
    emit OracleStopLossHighWatermarkUpdated(pool_, pending.binIdx, pending.token0, pending.token1);
  }
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
