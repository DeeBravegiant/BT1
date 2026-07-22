### Title
Pool Admin Can Front-Run `collectPoolFees` to Redirect Accrued Admin Fees via `setPoolAdminFeeDestination` — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`setPoolAdminFeeDestination` updates `poolAdminFeeDestination[pool]` without first collecting already-accrued fees. Because `collectPoolFees` is **permissionless**, the pool admin can observe a pending `collectPoolFees` transaction in the mempool and front-run it with a destination change, redirecting all accrued admin-fee tokens to an address of their choosing instead of the originally configured recipient.

---

### Finding Description

The protocol enforces a **collect-first** pattern in every function that changes how fees are split or rated:

- `setPoolAdminFees` calls `collectFees(…, poolAdminFeeDestination[pool])` **before** updating rates.
- `setPoolProtocolFee` calls `collectFees(…, poolAdminFeeDestination[pool])` **before** updating rates.

This ensures that fees accrued under the old configuration are settled to the old destination before any new configuration takes effect.

`setPoolAdminFeeDestination` breaks this invariant:

```solidity
// MetricOmmPoolFactory.sol L438-447
function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (newAdminFeeDestination == address(0)) revert InvalidAdminFeeDestination();
    poolAdminFeeDestination[pool] = newAdminFeeDestination;   // ← no prior collectFees
    emit PoolAdminFeeDestinationUpdated(pool, newAdminFeeDestination);
}
``` [1](#0-0) 

Meanwhile, `collectPoolFees` is explicitly permissionless — any address may call it:

```solidity
// MetricOmmPoolFactory.sol L379-389
function collectPoolFees(address pool) external override nonReentrant {
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool).collectFees(
        c.protocolSpreadFeeE6, c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8, c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]   // ← reads current destination at call time
    );
}
``` [2](#0-1) 

The `poolAdmin` and `poolAdminFeeDestination` are **separate parameters** at pool creation, meaning the admin key and the fee recipient can be distinct entities (e.g., admin = multisig, destination = DAO treasury): [3](#0-2) 

The `collectFees` function on the pool distributes admin fees to whatever `adminFeeDestination_` is passed in at call time — it has no memory of the destination that was active when fees were earned: [4](#0-3) 

---

### Impact Explanation

A pool admin can retroactively redirect all admin fees that accrued while `adminFeeDestination` pointed to a legitimate recipient (e.g., a DAO treasury) to any address they control. The legitimate destination receives zero tokens for the entire accrual period. This is a direct loss of accrued fee tokens for the intended recipient.

The contrast with `setPoolAdminFees` — which **does** collect first — confirms the protocol's own intent: fee configuration changes should not retroactively reassign already-earned value. [5](#0-4) 

---

### Likelihood Explanation

- `collectPoolFees` is permissionless and is expected to be called by keepers or bots on a regular schedule, making pending transactions observable in the mempool.
- The pool admin can also trigger the attack without front-running: call `setPoolAdminFeeDestination` at any time, then call `collectPoolFees` themselves.
- No timelock, no two-step process, and no collect-first guard exist on `setPoolAdminFeeDestination`.
- The scenario is realistic whenever `poolAdmin` and `poolAdminFeeDestination` are different entities (e.g., a multisig admin managing a DAO treasury destination).

---

### Recommendation

Apply the same collect-first pattern used by `setPoolAdminFees` and `setPoolProtocolFee`:

```solidity
function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (newAdminFeeDestination == address(0)) revert InvalidAdminFeeDestination();

    // Settle fees to the OLD destination before switching
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool).collectFees(
        c.protocolSpreadFeeE6, c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8, c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]   // old destination
    );

    poolAdminFeeDestination[pool] = newAdminFeeDestination;
    emit PoolAdminFeeDestinationUpdated(pool, newAdminFeeDestination);
}
```

This ensures fees earned under the old destination are settled before the new one takes effect, matching the invariant already enforced by `setPoolAdminFees`.

---

### Proof of Concept

**Setup:**
- Pool deployed with `admin = adminMultisig`, `adminFeeDestination = daoTreasury`.
- Fees accumulate over time (spread surplus + notional accumulators).

**Attack (front-run variant):**
1. A keeper submits `factory.collectPoolFees(pool)` to the mempool.
2. `adminMultisig` observes the pending transaction and front-runs with:
   ```
   factory.setPoolAdminFeeDestination(pool, attackerEOA)
   ```
   No fees are collected; `poolAdminFeeDestination[pool]` is now `attackerEOA`.
3. The keeper's `collectPoolFees` executes. Inside `collectFees`:
   - `adminFeeDestination_` = `attackerEOA` (the just-updated value).
   - All admin-share tokens are transferred to `attackerEOA`.
4. `daoTreasury` receives **zero** tokens despite being the intended recipient for the entire accrual period.

**Attack (no front-run needed):**
1. Fees accumulate with `adminFeeDestination = daoTreasury`.
2. Admin calls `setPoolAdminFeeDestination(pool, attackerEOA)`.
3. Admin calls `collectPoolFees(pool)` — all accrued admin fees go to `attackerEOA`. [1](#0-0) [2](#0-1)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L212-220)
```text
    poolAdmin[pool] = params.admin;
    priceProviderTimelock[pool] = params.priceProviderTimelock;
    poolFeeConfig[pool] = PoolFeeConfig({
      protocolSpreadFeeE6: spreadProtocolFeeE6,
      adminSpreadFeeE6: params.adminSpreadFeeE6,
      protocolNotionalFeeE8: protocolNotionalFeeE8,
      adminNotionalFeeE8: params.adminNotionalFeeE8
    });
    poolAdminFeeDestination[pool] = params.adminFeeDestination;
```

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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L417-425)
```text
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L438-447)
```text
  function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    if (newAdminFeeDestination == address(0)) revert InvalidAdminFeeDestination();
    poolAdminFeeDestination[pool] = newAdminFeeDestination;
    emit PoolAdminFeeDestinationUpdated(pool, newAdminFeeDestination);
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
