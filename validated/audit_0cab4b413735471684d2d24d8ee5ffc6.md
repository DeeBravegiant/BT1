### Title
Immutable `FACTORY` address in `collectFees` causes permanent protocol fee lock when blacklisted by USDC/USDT — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool` stores the factory address as `address internal immutable FACTORY`. Every call to `collectFees` unconditionally transfers the protocol share of accrued fees to `FACTORY` via `safeTransfer`. If `FACTORY` is blacklisted by the USDC or USDT operator, every `collectFees` invocation reverts, permanently locking all accrued protocol fees (both spread-surplus and notional-fee balances) inside the pool with no rescue path.

---

### Finding Description

`FACTORY` is burned into the pool bytecode at construction and can never be changed: [1](#0-0) 

Inside `collectFees`, after computing the protocol share, the pool calls: [2](#0-1) 

`transferToken0` / `transferToken1` are thin wrappers around `IERC20.safeTransfer`: [3](#0-2) 

`safeTransfer` to a USDC/USDT-blacklisted address reverts. Because `notionalFeeToken0Scaled` and `notionalFeeToken1Scaled` are zeroed **after** the transfers, a revert rolls back the entire transaction, leaving the accounting intact but the fees permanently unclaimable: [4](#0-3) 

Every entry point that could unblock the situation also calls `collectFees` first with the **current** (non-zero) protocol rates, so they all revert too:

- `collectPoolFees` (permissionless): [5](#0-4) 
- `setPoolProtocolFee` (owner-only, would set protocol fees to 0): [6](#0-5) 
- `setPoolAdminFees` (pool-admin): [7](#0-6) 

The factory's `collectTokens` rescue function only moves tokens **already at the factory**; it cannot reach tokens held by the pool: [8](#0-7) 

`adminFeeDestination` is updatable via `setPoolAdminFeeDestination`, but `FACTORY` (the protocol-fee recipient) has no equivalent setter anywhere in the system. [9](#0-8) 

---

### Impact Explanation

Two categories of real token balances become permanently unrecoverable:

1. **Notional fees** — tracked explicitly in `notionalFeeToken0Scaled` / `notionalFeeToken1Scaled`; they accumulate with every swap and can never be zeroed once `collectFees` is bricked.
2. **Spread-fee surplus** — the implicit excess (`balance - binTotals - notionalFees`) that grows with every swap; it is computed on the fly and transferred in the same call, so it is equally stuck.

LP principal is unaffected (it is tracked in `binTotals`), but all protocol revenue from the affected pool is lost forever. There is no upgrade path, no proxy, and no factory-level override that can redirect the transfer away from the blacklisted `FACTORY`.

---

### Likelihood Explanation

USDC and USDT operators (Circle, Tether) can and do blacklist smart-contract addresses, not only EOAs. The scope explicitly includes USDC/USDT non-standard behavior. The factory is a high-value, publicly known contract; regulatory action, sanctions compliance, or an exploit involving the factory address are all realistic triggers. Once blacklisted, the deadlock is permanent because no in-protocol path can change `FACTORY` or bypass the transfer.

---

### Recommendation

Replace the hardcoded `FACTORY` transfer target with an updatable `protocolFeeRecipient` storage variable on the pool (or on the factory, passed into `collectFees` as a parameter alongside `adminFeeDestination`). The factory owner should be able to rotate this address without requiring a full pool redeployment. If the recipient must remain the factory itself, add a fallback path in `collectFees` that skips the protocol transfer (accumulating the amount in a separate claimable mapping) rather than reverting, so LP withdrawals and admin-fee collection remain functional even when the protocol recipient is temporarily unreachable.

---

### Proof of Concept

```
1. Deploy a USDC/USDT pool via MetricOmmPoolFactory.createPool().
   FACTORY = address(factory) is burned into the pool as an immutable.

2. LPs add liquidity; swaps accrue spread-fee surplus and notional fees
   (notionalFeeToken0Scaled > 0, notionalFeeToken1Scaled > 0).

3. USDC operator blacklists address(factory).

4. Anyone calls MetricOmmPoolFactory.collectPoolFees(pool):
     → pool.collectFees(protocolSpread, adminSpread, protocolNotional, adminNotional, adminDest)
     → totalFee0ToProtocol > 0
     → IERC20(USDC).safeTransfer(FACTORY, totalFee0ToProtocol)   ← REVERTS (blacklisted)
   Transaction reverts; notionalFeeToken0Scaled unchanged.

5. Factory owner calls setPoolProtocolFee(pool, 0, 0) to try to zero out protocol fees:
     → same collectFees call with old rates fires first → same revert.

6. Pool admin calls setPoolAdminFees(pool, 0, 0):
     → same collectFees call fires first → same revert.

7. No further path exists to move or zero the stuck balances.
   Protocol fees are permanently locked in the pool.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L44-44)
```text
  address internal immutable FACTORY;
```

**File:** metric-core/contracts/MetricOmmPool.sol (L422-427)
```text
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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L262-269)
```text
  function collectTokens(address token, address to, uint256 amount) external override onlyOwner {
    uint256 balance = IERC20(token).balanceOf(address(this));
    uint256 amountToCollect = amount == 0 ? balance : amount;
    if (amountToCollect > 0) {
      IERC20(token).safeTransfer(to, amountToCollect);
      emit TokensCollected(token, to, amountToCollect);
    }
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L318-335)
```text
  function setPoolProtocolFee(address pool, uint24 newProtocolSpreadFeeE6, uint24 newProtocolNotionalFeeE8)
    external
    override
    onlyOwner
    nonReentrant
  {
    if (newProtocolSpreadFeeE6 > maxProtocolSpreadFeeE6) revert ProtocolFeeTooHigh();
    if (newProtocolNotionalFeeE8 > maxProtocolNotionalFeeE8) revert ProtocolFeeTooHigh();

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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L408-425)
```text
  function setPoolAdminFees(address pool, uint24 newAdminSpreadFeeE6, uint24 newAdminNotionalFeeE8)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();

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
