The code matches the claim exactly. All four cited code paths are confirmed in the actual repository.

Audit Report

## Title
`protocolUnpausePool` Sets `pauseLevel = 1` Instead of `pauseLevel = 0`, Permanently Blocking Swaps After Protocol Pause/Unpause Cycle — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

## Summary
`protocolUnpausePool` calls `setPause(1)` (admin-paused) instead of `setPause(0)` (active). Because `_checkNotPaused` rejects any non-zero pause level, a pool that was fully active before a protocol pause remains swap-blocked after the protocol owner "unpauses" it. The protocol owner has no further lever to reach level 0; only the pool admin can do so via `unpausePool`.

## Finding Description
The pause system has three levels: 0 = active, 1 = admin-paused, 2 = protocol-paused.

`protocolPausePool` (L392–396) accepts source states 0 **and** 1, so a live active pool (level 0) is a valid target:
```solidity
if (cur != 0 && cur != 1) revert InvalidPauseTransition(cur, 2);
IMetricOmmPoolFactoryActions(pool).setPause(2);
```

`protocolUnpausePool` (L399–403) then unconditionally sets level **1**, not level 0:
```solidity
if (cur != 2) revert InvalidPauseTransition(cur, 1);
IMetricOmmPoolFactoryActions(pool).setPause(1);   // ← should be 0
```

`_checkNotPaused` (L643–644) in `MetricOmmPool` rejects every non-zero level:
```solidity
if (pauseLevel != 0) revert PoolPaused();
```

`unpausePool` (L467–471) is gated to `onlyPoolAdmin` and is the only function that can set level 0. The protocol owner cannot call it. If the pool admin is unresponsive or the key is lost, the pool is permanently stuck at level 1 despite the protocol owner having called "unpause."

## Impact Explanation
After a single protocol pause/unpause cycle on any active pool, all swaps revert with `PoolPaused`. LP fee accrual stops. The protocol owner cannot unilaterally restore the pool; recovery requires pool-admin cooperation. If the pool admin is unavailable, the pool is permanently non-functional. This is broken core pool functionality causing unusable swap flows, matching the allowed impact gate.

## Likelihood Explanation
Triggered on every protocol pause/unpause cycle applied to a pool at level 0 — the normal state of any live pool. No special setup or attacker is required; the protocol owner exercising their documented authority is sufficient. The condition is repeatable and deterministic.

## Recommendation
Change `protocolUnpausePool` to call `setPause(0)` and update the transition guard to target 0:
```solidity
function protocolUnpausePool(address pool) external override nonReentrant onlyOwner {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 2) revert InvalidPauseTransition(cur, 0);
    IMetricOmmPoolFactoryActions(pool).setPause(0); // restore to active
}
```
Alternatively, record the pre-pause level in factory storage when `protocolPausePool` is called and restore it here.

## Proof of Concept
1. Pool deployed and active: `pauseLevel = 0`.
2. Protocol owner calls `protocolPausePool(pool)` — passes guard (`cur == 0`). Pool: `pauseLevel = 2`.
3. Protocol owner calls `protocolUnpausePool(pool)` — passes guard (`cur == 2`), calls `setPause(1)`. Pool: `pauseLevel = 1`.
4. Any trader calls `swap(...)` → `_checkNotPaused` fires → reverts `PoolPaused` (`pauseLevel == 1 != 0`).
5. Protocol owner has no further function to set level 0; `unpausePool` is `onlyPoolAdmin`.
6. If `poolAdmin[pool]` is unresponsive, pool is permanently paused.

Foundry test skeleton:
```solidity
factory.protocolPausePool(pool);
factory.protocolUnpausePool(pool);
// pauseLevel is now 1, not 0
vm.expectRevert(PoolPaused.selector);
pool.swap(...);
```