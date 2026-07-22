### Title
`OracleValueStopLossExtension` stale `executeAfter` snapshot lets pool admin bypass a subsequently-raised timelock for pending parameter changes — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

Every parameter-change proposal in `OracleValueStopLossExtension` (drawdown, decay, high-watermarks) snapshots `executeAfter = block.timestamp + currentTimelock` at proposal time. The timelock itself is mutable via `proposeOracleStopLossTimelock` / `executeOracleStopLossTimelock`. If the timelock is raised after a proposal is already pending, the pending proposal's `executeAfter` is stale: it was computed against the old, shorter timelock. The admin can execute the pending proposal immediately after the timelock increase, bypassing the new longer delay that LPs are now relying on.

---

### Finding Description

`_afterTimelock` snapshots the delay at proposal time:

```solidity
// OracleValueStopLossExtension.sol:297-299
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

Every propose function calls this once and stores the result:

```solidity
// OracleValueStopLossExtension.sol:106-109
uint32 executeAfter = _afterTimelock(pool_);
sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
sched.pendingDrawdownExecuteAfter = executeAfter;
```

The execute function only checks whether the stored `executeAfter` has elapsed; it never re-reads the live timelock:

```solidity
// OracleValueStopLossExtension.sol:112-119
function executeOracleStopLossDrawdown(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingDrawdownExecuteAfter == 0) revert OracleStopLossNoPendingDrawdown(pool_);
    _requireElapsed(sched.pendingDrawdownExecuteAfter);   // stale snapshot
    ...
}
```

The same pattern applies to `proposeOracleStopLossDecay` / `executeOracleStopLossDecay` and `proposeOracleStopLossHighWatermarks` / `executeOracleStopLossHighWatermarks`.

**Attack sequence (all calls by pool admin):**

| Step | Action | Timelock | `executeAfter` stored |
|------|--------|----------|-----------------------|
| 1 | Pool initialized with `timelock = 1 day` | 1 day | — |
| 2 | `proposeOracleStopLossDrawdown(pool, 0)` | 1 day | `T₀ + 1 day` |
| 3 | `proposeOracleStopLossTimelock(pool, 7 days)` | 1 day | `T₀ + 1 day` |
| 4 | `warp(T₀ + 1 day)` then `executeOracleStopLossTimelock` | **7 days** | — |
| 5 | `executeOracleStopLossDrawdown` succeeds immediately | 7 days | already elapsed |

After step 5, `drawdownE6 = 0` (stop-loss disabled) took effect after only 1 day, even though the live timelock is 7 days. LPs who observed the timelock increase at step 4 believe they have 7 days of notice for any parameter change; the pre-staged proposal at step 2 silently bypasses that guarantee.

The same sequence works for decay and high-watermark proposals. The high-watermark variant is particularly impactful: the admin can raise watermarks to artificially high values, then immediately execute, causing the stop-loss to trigger on the very next swap and freeze the pool in one direction.

---

### Impact Explanation

The `OracleValueStopLossExtension` is documented as protecting LPs: *"Drawdown and decay changes are timelocked so LPs can react."* The stale `executeAfter` breaks this invariant. A pool admin can:

1. **Disable stop-loss protection** (`drawdownE6 → 0`) with only the old short timelock, then let value-extracting swaps proceed unblocked.
2. **Freeze swap directions** by setting watermarks to artificially high values and executing immediately after a timelock raise, blocking legitimate LP withdrawals via swap.
3. **Deceive LPs** by publicly raising the timelock (signalling trustworthiness) while a pre-staged harmful proposal is already executable.

This is a direct admin-boundary break: the pool admin bypasses the timelock that LPs rely on to react to parameter changes, potentially causing direct loss of LP assets.

---

### Likelihood Explanation

The pool admin is a semi-trusted role with explicit control over stop-loss parameters. The attack requires deliberate sequencing (propose before raising timelock), but no external conditions, no oracle manipulation, and no special token behavior. Any pool admin who wishes to extract value or freeze a pool can execute this sequence in two transactions separated by the old (short) timelock. LPs who joined after the proposal was made, or who did not notice the pending proposal event before the timelock increase, have no on-chain recourse once the execute fires.

---

### Recommendation

At execution time, re-validate against the live timelock rather than relying solely on the snapshotted `executeAfter`. Store the proposal timestamp and enforce:

```diff
 function executeOracleStopLossDrawdown(address pool_) external onlyPoolAdmin(pool_) {
     PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
     if (sched.pendingDrawdownExecuteAfter == 0) revert OracleStopLossNoPendingDrawdown(pool_);
     _requireElapsed(sched.pendingDrawdownExecuteAfter);
+    // Re-enforce live timelock: proposal must also be at least currentTimelock old.
+    uint32 liveDeadline = sched.pendingDrawdownProposedAt + oracleStopLossConfig[pool_].timelock;
+    _requireElapsed(liveDeadline);
     ...
 }
```

Apply the same fix to decay and high-watermark execute functions. Store `proposedAt = uint32(block.timestamp)` in each `PoolStopLossSchedule` field at proposal time.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import "forge-std/Test.sol";
import "../../contracts/extensions/OracleValueStopLossExtension.sol";
import "../../contracts/interfaces/extensions/IOracleValueStopLossExtension.sol";

contract PoC_StaleExecuteAfterBypassesRaisedTimelock is Test {
    OracleValueStopLossExtension extension;
    address pool = address(0xBEEF);
    address admin = address(0xAD);
    address factory = address(this);

    // Factory stub
    function getFeeCaps() external pure returns (uint24, uint24, uint24, uint24) {
        return (200_000, 200_000, 1_000_000, 1_000_000);
    }
    mapping(address => address) public poolAdmin;

    function setUp() public {
        poolAdmin[pool] = admin;
        extension = new OracleValueStopLossExtension(address(this));
        // Initialize with 1-day timelock and 50% drawdown
        extension.initialize(pool, abi.encode(uint32(500_000), uint32(58), uint32(1 days)));
    }

    function test_staleExecuteAfter_bypassesRaisedTimelock() public {
        // Step 2: propose drawdown = 0 (disable stop-loss) under 1-day timelock
        vm.prank(admin);
        extension.proposeOracleStopLossDrawdown(pool, 0);

        // Step 3: propose timelock increase to 7 days (also under 1-day timelock)
        vm.prank(admin);
        extension.proposeOracleStopLossTimelock(pool, 7 days);

        // Step 4: after 1 day, execute timelock increase
        vm.warp(block.timestamp + 1 days);
        vm.prank(admin);
        extension.executeOracleStopLossTimelock(pool);

        (,, uint32 tl,) = extension.oracleStopLossConfig(pool);
        assertEq(tl, 7 days, "timelock is now 7 days");

        // Step 5: immediately execute drawdown change — bypasses 7-day timelock
        vm.prank(admin);
        extension.executeOracleStopLossDrawdown(pool); // should revert if fix applied

        (uint32 dd,,,) = extension.oracleStopLossConfig(pool);
        assertEq(dd, 0, "stop-loss disabled after only 1 day, not 7");
    }
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L103-110)
```text
  function proposeOracleStopLossDrawdown(address pool_, uint256 newMaxDrawdownE6) external onlyPoolAdmin(pool_) {
    _validateDrawdown(newMaxDrawdownE6);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
    sched.pendingDrawdownExecuteAfter = executeAfter;
    emit OracleStopLossDrawdownProposed(pool_, newMaxDrawdownE6, executeAfter);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L112-120)
```text
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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L156-166)
```text
  /// @notice Propose per-bin high watermarks; applied after the pool timelock via execute.
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
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L169-177)
```text
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
