### Title
USDC-Blacklisted `adminFeeDestination` Permanently Freezes Fee Collection and Fee-Rate Governance — (`metric-core/contracts/MetricOmmPool.sol`)

### Summary

`collectFees` in `MetricOmmPool.sol` uses push-based `safeTransfer` to send the admin share of accrued fees directly to `adminFeeDestination_`. If that address is USDC-blacklisted, every call to `collectFees` reverts. Because `setPoolProtocolFee` and `setPoolAdminFees` in the factory both call `collectFees` before applying new rates, a blacklisted `adminFeeDestination` permanently freezes fee collection and blocks all fee-rate governance updates. No setter for `poolAdminFeeDestination[pool]` exists in the factory, so the deadlock cannot be broken.

---

### Finding Description

`MetricOmmPool.collectFees` distributes accrued spread and notional fees to two destinations in sequence:

```solidity
if (totalFee0ToAdmin > 0) {
    transferToken0(adminFeeDestination_, totalFee0ToAdmin);   // push
}
if (totalFee1ToAdmin > 0) {
    transferToken1(adminFeeDestination_, totalFee1ToAdmin);   // push
}
if (totalFee0ToProtocol > 0) {
    transferToken0(FACTORY, totalFee0ToProtocol);
}
if (totalFee1ToProtocol > 0) {
    transferToken1(FACTORY, totalFee1ToProtocol);
}
``` [1](#0-0) 

`transferToken1` resolves to `IERC20(TOKEN1).safeTransfer(adminFeeDestination_, amount)`. If `TOKEN1` is USDC and `adminFeeDestination_` is on Circle's blacklist, `safeTransfer` reverts and the entire `collectFees` call reverts.

`adminFeeDestination_` is not a parameter the pool stores; it is read from `poolAdminFeeDestination[pool]` in the factory and passed in at call time:

```solidity
function collectPoolFees(address pool) external override nonReentrant {
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool).collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]   // ← immutable after createPool
    );
}
``` [2](#0-1) 

`poolAdminFeeDestination[pool]` is written once at `createPool` and no setter exists in the factory. The deadlock therefore cannot be resolved by the pool admin or the protocol owner.

The same `collectFees` call is embedded inside both fee-governance functions:

```solidity
// inside setPoolProtocolFee (onlyOwner)
IMetricOmmPoolCollectFees(pool).collectFees(
    c.protocolSpreadFeeE6, c.adminSpreadFeeE6,
    c.protocolNotionalFeeE8, c.adminNotionalFeeE8,
    poolAdminFeeDestination[pool]
);
``` [3](#0-2) 

```solidity
// inside setPoolAdminFees (onlyPoolAdmin)
IMetricOmmPoolCollectFees(pool).collectFees(
    c.protocolSpreadFeeE6, c.adminSpreadFeeE6,
    c.protocolNotionalFeeE8, c.adminNotionalFeeE8,
    poolAdminFeeDestination[pool]
);
``` [4](#0-3) 

Once `adminFeeDestination_` is blacklisted and admin fees are non-zero, all three entry points (`collectPoolFees`, `setPoolProtocolFee`, `setPoolAdminFees`) revert. The only escape would be to set admin fee rates to zero, but doing so requires calling `setPoolAdminFees`, which itself calls `collectFees` first — a circular dependency.

---

### Impact Explanation

1. **Protocol and admin fees permanently locked in the pool.** All spread surplus and notional fee accumulators (`notionalFeeToken0Scaled`, `notionalFeeToken1Scaled`) remain in the pool contract indefinitely. Neither the protocol treasury nor the admin can extract them. [5](#0-4) 

2. **Fee-rate governance frozen.** The protocol owner cannot call `setPoolProtocolFee` and the pool admin cannot call `setPoolAdminFees`. The pool is permanently locked at its current fee configuration with no recovery path. [6](#0-5) 

Swaps and liquidity operations are **not** blocked — `collectFees` is not on the swap or liquidity path — so user principal is safe. The loss is confined to accrued protocol and admin fees.

---

### Likelihood Explanation

- The pool must use USDC (or USDT) as one of its tokens. Metric OMM is explicitly deployed on Ethereum and Base where USDC pools are expected.
- `adminFeeDestination_` must be blacklisted by Circle. This can happen post-deployment if the destination address interacts with sanctioned entities, is compromised, or is a smart contract that Circle blacklists.
- The condition is external but not implausible for a live protocol over a multi-year horizon.
- Because `poolAdminFeeDestination[pool]` has no setter, there is zero recovery path once the condition is met.

---

### Recommendation

Replace the push-based admin fee transfer with a pull (claim) pattern:

1. Accumulate admin fees in a per-pool mapping inside the factory (or on the pool itself).
2. Expose a `claimAdminFees(address pool, address to)` function callable only by the pool admin, where `to` is supplied at claim time rather than stored at creation.
3. Decouple `collectFees` from `setPoolProtocolFee` / `setPoolAdminFees` so fee-rate governance does not depend on a successful token transfer.

Alternatively, add a `setPoolAdminFeeDestination(address pool, address newDest)` function (callable by the pool admin) so the destination can be rotated if it becomes blacklisted.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.35;

// Assume: pool has USDC as token1, adminSpreadFeeE6 > 0,
//         adminFeeDestination = 0xVictim, some swaps have occurred.

// Step 1: Circle blacklists 0xVictim (mocked below).
vm.mockCallRevert(
    address(usdc),
    abi.encodeWithSelector(IERC20.transfer.selector, victim, /* any amount */),
    "Blacklisted"
);

// Step 2: permissionless collectPoolFees reverts.
vm.expectRevert("Blacklisted");
factory.collectPoolFees(address(pool));

// Step 3: protocol owner cannot update protocol fee.
vm.prank(protocolOwner);
vm.expectRevert("Blacklisted");
factory.setPoolProtocolFee(address(pool), newProtocolFee, 0);

// Step 4: pool admin cannot update admin fee (including setting it to 0).
vm.prank(poolAdmin);
vm.expectRevert("Blacklisted");
factory.setPoolAdminFees(address(pool), 0, 0);

// Result: fees are permanently locked; fee configuration is permanently frozen.
```

<cite repo="Tylerpinwa/2026-07-metric-dev-oyakhil-main--016" path="metric-core/contracts/MetricO

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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L318-360)
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

    uint24 aSpread = c.adminSpreadFeeE6;
    uint24 aNotional = c.adminNotionalFeeE8;
    if (aSpread > maxAdminSpreadFeeE6) {
      aSpread = maxAdminSpreadFeeE6;
      emit PoolAdminSpreadFeeUpdated(pool, aSpread);
    }
    if (aNotional > maxAdminNotionalFeeE8) {
      aNotional = maxAdminNotionalFeeE8;
      emit PoolAdminNotionalFeeUpdated(pool, aNotional);
    }

    c = PoolFeeConfig({
      protocolSpreadFeeE6: newProtocolSpreadFeeE6,
      adminSpreadFeeE6: aSpread,
      protocolNotionalFeeE8: newProtocolNotionalFeeE8,
      adminNotionalFeeE8: aNotional
    });
    poolFeeConfig[pool] = c;

    IMetricOmmPoolFactoryActions(pool)
      .setPoolFees(c.protocolSpreadFeeE6 + c.adminSpreadFeeE6, c.protocolNotionalFeeE8 + c.adminNotionalFeeE8);
    emit PoolProtocolSpreadFeeUpdated(pool, newProtocolSpreadFeeE6);
    emit PoolProtocolNotionalFeeUpdated(pool, newProtocolNotionalFeeE8);
  }
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
