Audit Report

## Title
Stale `poolAdminFeeDestination` After Admin Transfer Misdirects Accrued Admin Fees — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

## Summary
`acceptPoolAdmin` updates `poolAdmin[pool]` but never resets `poolAdminFeeDestination[pool]`, leaving the old admin's fee destination in place. Because `collectPoolFees` is permissionless, the old admin (or any caller) can immediately drain all accrued admin-share fees to the stale address after the handover. The new admin's own first `setPoolAdminFees` call also internally flushes fees to the stale destination before the new admin can update it.

## Finding Description
`acceptPoolAdmin` (L518–526) writes only to `poolAdmin[pool]` and clears `pendingPoolAdmin[pool]`; `poolAdminFeeDestination[pool]` is a separate mapping that is never touched:

```solidity
// MetricOmmPoolFactory.sol L518-526
poolAdmin[pool] = pending;
delete pendingPoolAdmin[pool];
// poolAdminFeeDestination[pool] ← never updated, still old admin's address
```

`collectPoolFees` (L379–389) carries no access-control modifier — only `nonReentrant` — and passes the stale mapping value directly to `IMetricOmmPoolCollectFees.collectFees`:

```solidity
// MetricOmmPoolFactory.sol L379-389
function collectPoolFees(address pool) external override nonReentrant {
    ...
    poolAdminFeeDestination[pool]   // ← stale old-admin address
```

`setPoolAdminFees` (L418–425), callable only by the new admin, also flushes fees to the stale destination before updating rates:

```solidity
// MetricOmmPoolFactory.sol L418-425
IMetricOmmPoolCollectFees(pool).collectFees(
    ...,
    poolAdminFeeDestination[pool]   // ← still stale at this point
);
```

Inside `MetricOmmPool.collectFees` (L416–421), the admin share is transferred unconditionally to whatever address is passed:

```solidity
// MetricOmmPool.sol L416-421
if (totalFee0ToAdmin > 0) transferToken0(adminFeeDestination_, totalFee0ToAdmin);
if (totalFee1ToAdmin > 0) transferToken1(adminFeeDestination_, totalFee1ToAdmin);
```

No existing guard prevents this: `collectPoolFees` is fully open, and `setPoolAdminFeeDestination` (L438–447) requires the caller to already be the new `poolAdmin`, but by the time the new admin calls it, fees may already be drained.

## Impact Explanation
The new pool admin suffers a direct, permanent loss of token0 and token1 equal to the full admin fee share outstanding at the time of the first post-transfer `collectPoolFees` call. This is a direct loss of protocol/admin fee assets — matching the allowed impact gate for Critical/High direct loss of protocol fees. The loss magnitude is bounded only by fees accrued since the last collection; for active pools with non-zero `adminSpreadFeeE6` or `adminNotionalFeeE8`, this is material.

## Likelihood Explanation
Admin transfers are a documented, first-class lifecycle event. The old admin has both motive and knowledge to call the permissionless `collectPoolFees` in the same block as `acceptPoolAdmin` (or front-run the new admin's `setPoolAdminFeeDestination` transaction). No special privilege is required — only knowledge of the pool address, which is public. The attack is repeatable on every admin transfer where fees have accrued.

## Recommendation
In `acceptPoolAdmin`, atomically reset `poolAdminFeeDestination[pool]` to the incoming admin's address (or to a sentinel that blocks collection until explicitly set):

```solidity
function acceptPoolAdmin(address pool) external override nonReentrant {
    address pending = pendingPoolAdmin[pool];
    if (pending == address(0)) revert NoPendingPoolAdminTransfer();
    if (msg.sender != pending) revert NotPendingPoolAdmin(pool, msg.sender, pending);
    address previousAdmin = poolAdmin[pool];
    poolAdmin[pool] = pending;
    delete pendingPoolAdmin[pool];
+   poolAdminFeeDestination[pool] = pending; // reset; new admin updates via setPoolAdminFeeDestination
    emit PoolAdminTransferred(pool, previousAdmin, pending);
}
```

Alternatively, add an overload accepting a `newFeeDestination` parameter to write both mappings atomically in one transaction.

## Proof of Concept
```
Setup:
  pool deployed; poolAdminFeeDestination[pool] = oldAdminWallet
  adminSpreadFeeE6 > 0; swaps occur → admin fee surplus accumulates

Step 1: oldAdmin calls proposePoolAdminTransfer(pool, newAdmin)
Step 2: newAdmin calls acceptPoolAdmin(pool)
        → poolAdmin[pool] = newAdmin
        → poolAdminFeeDestination[pool] == oldAdminWallet  ← unchanged

Step 3: oldAdmin (or MEV bot) calls collectPoolFees(pool)  ← no access control
        → collectFees(..., poolAdminFeeDestination[pool])
        → transferToken0(oldAdminWallet, adminShare0)      ← loss to newAdmin
        → transferToken1(oldAdminWallet, adminShare1)      ← loss to newAdmin

Step 4: newAdmin calls setPoolAdminFeeDestination(pool, newAdminWallet)
        → too late; all accrued fees already sent to oldAdminWallet

Alternatively (Step 3'): newAdmin calls setPoolAdminFees(pool, ...)
        → internally calls collectFees(..., poolAdminFeeDestination[pool])
        → fees sent to oldAdminWallet even though newAdmin initiated the call
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L379-389)
```text
  function collectPoolFees(address pool) external override nonReentrant {
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L418-425)
```text
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L518-526)
```text
  function acceptPoolAdmin(address pool) external override nonReentrant {
    address pending = pendingPoolAdmin[pool];
    if (pending == address(0)) revert NoPendingPoolAdminTransfer();
    if (msg.sender != pending) revert NotPendingPoolAdmin(pool, msg.sender, pending);
    address previousAdmin = poolAdmin[pool];
    poolAdmin[pool] = pending;
    delete pendingPoolAdmin[pool];
    emit PoolAdminTransferred(pool, previousAdmin, pending);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L416-421)
```text
      if (totalFee0ToAdmin > 0) {
        transferToken0(adminFeeDestination_, totalFee0ToAdmin);
      }
      if (totalFee1ToAdmin > 0) {
        transferToken1(adminFeeDestination_, totalFee1ToAdmin);
      }
```
