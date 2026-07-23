### Title
Push-payment in `collectFees` to `adminFeeDestination` permanently blocks fee collection and fee-configuration updates when pool token is USDC — (`metric-core/contracts/MetricOmmPool.sol`, `metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`collectFees` in `MetricOmmPool` uses a push-payment pattern to transfer accrued fees directly to `adminFeeDestination_`. Because `collectFees` is called atomically inside `setPoolAdminFees` and `setPoolProtocolFee` in the factory, a USDC-blacklisted `adminFeeDestination` causes all three entry-points to revert permanently. Protocol fees accumulate in the pool but can never be extracted, and the protocol owner loses the ability to update fee rates for that pool.

---

### Finding Description

`collectFees` in `MetricOmmPool.sol` pushes tokens to `adminFeeDestination_` via `safeTransfer`: [1](#0-0) 

`transferToken0` / `transferToken1` are thin wrappers around `IERC20.safeTransfer`: [2](#0-1) 

`collectFees` is called as the **first step** inside both `setPoolAdminFees` and `setPoolProtocolFee` in the factory, before any state is updated: [3](#0-2) [4](#0-3) 

The permissionless `collectPoolFees` entry-point also calls `collectFees` with the stored destination: [5](#0-4) 

`adminFeeDestination` is set by the pool admin via `setPoolAdminFeeDestination`, which does **not** call `collectFees` and therefore succeeds independently: [6](#0-5) 

If `adminFeeDestination` is a USDC-blacklisted address — either because USDC blacklisted it externally after it was set, or because the pool admin deliberately set it to a blacklisted address — every call to `collectFees` reverts at the `safeTransfer` line. Because `setPoolAdminFees` and `setPoolProtocolFee` both call `collectFees` atomically before updating state, they also revert. The `notionalFeeToken0Scaled` / `notionalFeeToken1Scaled` accumulators are only cleared **after** the transfers succeed: [7](#0-6) 

So they keep growing while fees remain permanently locked inside the pool.

---

### Impact Explanation

- **Protocol fees permanently stuck**: All accrued spread and notional fees held by the pool cannot be extracted. The tokens remain in the pool balance but are inaccessible to both the protocol and the admin.
- **Fee configuration frozen**: The protocol owner (`onlyOwner`) cannot call `setPoolProtocolFee` for the affected pool, losing the ability to adjust protocol fee rates. The pool admin cannot call `setPoolAdminFees` either.
- **Permissionless collection broken**: `collectPoolFees` (callable by anyone, including keepers) always reverts, so no external actor can unblock the situation without pool-admin cooperation.

The only recovery path is the pool admin calling `setPoolAdminFeeDestination` to a non-blacklisted address. If the pool admin is unresponsive or acting maliciously, the protocol owner has no recourse — a lower-privilege role (pool admin) permanently blocks a higher-privilege role (protocol owner) from exercising fee governance.

---

### Likelihood Explanation

USDC blacklisting of arbitrary addresses is a documented, exercised capability. Any pool whose token0 or token1 is USDC and whose `adminFeeDestination` is ever blacklisted (by USDC, not necessarily by the pool admin) hits this path automatically. The pool admin can also trigger it deliberately by calling `setPoolAdminFeeDestination` with a known-blacklisted address, which requires no special privilege beyond being the pool admin.

---

### Recommendation

1. **Decouple fee collection from fee configuration**: `setPoolAdminFees` and `setPoolProtocolFee` should not call `collectFees` atomically. Collect fees in a separate, independent transaction.
2. **Wrap transfers in try/catch**: If atomic collection must be preserved, wrap each `safeTransfer` in a `try/catch` and emit an event on failure rather than reverting the entire call.
3. **Pull-over-push for fee destinations**: Accrue fees to a claimable balance mapping keyed by destination address; let recipients pull their own share.

---

### Proof of Concept

1. Deploy a pool with USDC as token0 and set `adminFeeDestination` to address `A`.
2. Execute several swaps to accumulate spread and notional fees.
3. USDC blacklists address `A` (or pool admin calls `setPoolAdminFeeDestination(pool, blacklistedAddress)`).
4. Call `factory.collectPoolFees(pool)` → reverts because `IERC20(USDC).safeTransfer(A, amount)` reverts.
5. Call `factory.setPoolProtocolFee(pool, newFee, newNotional)` as the protocol owner → reverts at the internal `collectFees` call before any state is written.
6. Call `factory.setPoolAdminFees(pool, newAdminFee, newAdminNotional)` as the pool admin → same revert.
7. `notionalFeeToken0Scaled` and `notionalFeeToken1Scaled` remain non-zero and keep growing with each subsequent swap; the tokens are permanently locked in the pool.

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

**File:** metric-core/contracts/MetricOmmPool.sol (L565-571)
```text
  function transferToken0(address to, uint256 amount) internal {
    IERC20(TOKEN0).safeTransfer(to, amount);
  }

  function transferToken1(address to, uint256 amount) internal {
    IERC20(TOKEN1).safeTransfer(to, amount);
  }
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
