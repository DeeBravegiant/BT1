### Title
`collectFees` Push-Transfers to `adminFeeDestination` and `FACTORY` Atomically — USDC Blacklisting of Either Recipient Bricks Fee Collection and Fee-Rate Management — (File: `metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.collectFees` pushes tokens to two recipients — `adminFeeDestination_` and the immutable `FACTORY` address — in a single atomic call using `safeTransfer`. If either address is USDC-blacklisted, every call to `collectFees` reverts. Because `collectPoolFees` (permissionless), `setPoolAdminFees` (pool admin), and `setPoolProtocolFee` (factory owner) all route through `collectFees`, all three are simultaneously bricked. Accrued protocol and admin fees remain locked in the pool until the pool admin rotates the fee destination via `setPoolAdminFeeDestination`.

---

### Finding Description

`MetricOmmPool.collectFees` computes the admin and protocol shares of both spread and notional fees, then pushes them out in four sequential `safeTransfer` calls:

```
transferToken0(adminFeeDestination_, totalFee0ToAdmin);   // line 417
transferToken1(adminFeeDestination_, totalFee1ToAdmin);   // line 420
transferToken0(FACTORY, totalFee0ToProtocol);             // line 423
transferToken1(FACTORY, totalFee1ToProtocol);             // line 426
``` [1](#0-0) 

All four transfers are inside a single `unchecked` block with no try/catch. If USDC (or any blacklist-capable token used as `token0` or `token1`) has blacklisted `adminFeeDestination_`, the first or second `safeTransfer` reverts and the entire transaction is rolled back — including the zeroing of `notionalFeeToken0Scaled` / `notionalFeeToken1Scaled` at lines 429–430. [2](#0-1) 

Three callers all depend on `collectFees` succeeding:

**1. `collectPoolFees` (permissionless)** — calls `collectFees` directly with no fallback: [3](#0-2) 

**2. `setPoolAdminFees` (pool admin)** — calls `collectFees` at old rates before writing new rates: [4](#0-3) 

**3. `setPoolProtocolFee` (factory owner)** — calls `collectFees` at old rates before writing new protocol rates: [5](#0-4) 

The only escape hatch is `setPoolAdminFeeDestination`, which updates `poolAdminFeeDestination[pool]` without triggering a collection: [6](#0-5) 

This means recovery requires the pool admin to act. If the pool admin is unresponsive, compromised, or is the one who deliberately set a blacklisted destination, the factory owner has no independent path to collect protocol fees or update protocol fee rates for that pool.

---

### Impact Explanation

- **Fee collection is bricked**: `collectPoolFees` (permissionless) reverts for the affected pool. Accrued notional fees (`notionalFeeToken0Scaled`, `notionalFeeToken1Scaled`) and spread-fee surplus remain locked in the pool contract.
- **Fee-rate management is bricked**: Both `setPoolAdminFees` and `setPoolProtocolFee` call `collectFees` as a mandatory first step. Neither can execute while the destination is blacklisted, so neither the pool admin nor the factory owner can update fee rates for the pool.
- **Cross-role dependency**: The factory owner's ability to manage protocol fees for a specific pool is gated on the pool admin maintaining a non-blacklisted fee destination. A malicious or unresponsive pool admin can exploit this to permanently block the factory owner from adjusting protocol fees on their pool.
- **Funds are not permanently lost** — they remain in the pool and become collectable once the pool admin rotates the destination — but the DoS window is unbounded if the pool admin does not act.

---

### Likelihood Explanation

- USDC and USDT both implement address blacklisting; the protocol explicitly targets stablecoin pairs.
- `adminFeeDestination` is a pool-admin-controlled address. It may be a multisig, a DAO treasury, or a smart contract — any of which could be blacklisted by Circle/Tether for regulatory reasons.
- The scenario does not require any exploit of the pool itself; it arises from a standard token-level action by a third party (the token issuer).
- The pool admin can also deliberately set a blacklisted address to grief the factory owner's fee management.

---

### Recommendation

Adopt a **pull pattern** for fee distribution:

1. In `collectFees`, instead of pushing tokens to `adminFeeDestination_` and `FACTORY`, credit owed amounts to per-address storage mappings (e.g., `claimable0[recipient]`, `claimable1[recipient]`).
2. Expose a separate `claimFees(address token)` function that lets each recipient pull their own balance independently.
3. This decouples fee accounting from fee delivery: a blacklisted destination cannot block the other recipient's collection or block fee-rate updates.

Alternatively, wrap each `safeTransfer` in a try/catch and emit an event on failure, leaving uncollectable amounts in a claimable mapping for later retry.

---

### Proof of Concept

1. Deploy a pool with USDC as `token0`.
2. Swaps occur; `notionalFeeToken0Scaled` accumulates.
3. USDC blacklists `poolAdminFeeDestination[pool]` (e.g., due to a regulatory action against the admin's treasury).
4. Any caller invokes `collectPoolFees(pool)` → `collectFees` → `transferToken0(adminFeeDestination_, ...)` → `safeTransfer` reverts → entire call reverts.
5. Pool admin calls `setPoolAdminFees(pool, newSpread, newNotional)` → same revert path → fee rates cannot be updated.
6. Factory owner calls `setPoolProtocolFee(pool, newProtocolSpread, newProtocolNotional)` → same revert path → protocol fee rates cannot be updated for this pool.
7. Accrued fees remain locked. The factory owner has no independent path to force a fee destination change; only the pool admin can call `setPoolAdminFeeDestination`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L416-427)
```text
      if (totalFee0ToAdmin > 0) {
        transferToken0(adminFeeDestination_, totalFee0ToAdmin);
      }
      if (totalFee1ToAdmin > 0) {
        transferToken1(adminFeeDestination_, totalFee1ToAdmin);
      }
      if (totalFee0ToProtocol > 0) {
        transferToken0(FACTORY, totalFee0ToProtocol);
      }
      if (totalFee1ToProtocol > 0) {
        transferToken1(FACTORY, totalFee1ToProtocol);
      }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L429-430)
```text
      notionalFeeToken0Scaled = 0;
      notionalFeeToken1Scaled = 0;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L328-335)
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
