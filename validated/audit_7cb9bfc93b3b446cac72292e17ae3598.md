### Title
Pool Admin Can Permanently Lock Protocol Fees by Setting a Reverting `adminFeeDestination` — (File: metric-core/contracts/MetricOmmPoolFactory.sol)

### Summary
The pool admin can call `setPoolAdminFeeDestination` to point `adminFeeDestination` at a contract that reverts on ERC-20 receipt. Because `collectFees` transfers to `adminFeeDestination_` before it transfers to `FACTORY`, any revert in the admin leg causes the entire `collectFees` call to revert. Every factory path that collects fees — including the permissionless `collectPoolFees` and the owner-only `setPoolProtocolFee` — is permanently bricked for that pool, locking all accrued protocol and admin fees inside the pool contract with no recovery path.

### Finding Description

`setPoolAdminFeeDestination` accepts any non-zero address without verifying that the address can receive tokens:

```solidity
// MetricOmmPoolFactory.sol:438-447
function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (newAdminFeeDestination == address(0)) revert InvalidAdminFeeDestination();
    poolAdminFeeDestination[pool] = newAdminFeeDestination;
    emit PoolAdminFeeDestinationUpdated(pool, newAdminFeeDestination);
}
```

`collectFees` on the pool transfers to `adminFeeDestination_` **first**, before the protocol (`FACTORY`) leg:

```solidity
// MetricOmmPool.sol:416-427
if (totalFee0ToAdmin > 0) {
    transferToken0(adminFeeDestination_, totalFee0ToAdmin);   // ← reverts here
}
if (totalFee1ToAdmin > 0) {
    transferToken1(adminFeeDestination_, totalFee1ToAdmin);
}
if (totalFee0ToProtocol > 0) {
    transferToken0(FACTORY, totalFee0ToProtocol);             // ← never reached
}
if (totalFee1ToProtocol > 0) {
    transferToken1(FACTORY, totalFee1ToProtocol);
}
notionalFeeToken0Scaled = 0;
notionalFeeToken1Scaled = 0;
```

`transferToken0/1` use `safeTransfer`, which propagates any revert from the destination. Because the notional-fee accumulators are cleared **after** the transfers, a revert leaves them intact — but the function can never complete, so they are permanently unclaimable.

Every factory entry-point that collects fees passes `poolAdminFeeDestination[pool]` unchanged:

| Caller | Access | Effect if blocked |
|---|---|---|
| `collectPoolFees` | permissionless | fee collection permanently DoS'd |
| `setPoolProtocolFee` | `onlyOwner` | owner cannot change protocol fee rates |
| `setPoolAdminFees` | `onlyPoolAdmin` | admin cannot change their own fee rates |

The factory owner has no bypass: `collectFees` is `onlyFactory`, and the factory always forwards `poolAdminFeeDestination[pool]` — there is no overload that accepts an alternative destination.

### Impact Explanation
All accrued spread fees (held as pool balance surplus) and notional fees (`notionalFeeToken0Scaled` / `notionalFeeToken1Scaled`) are permanently locked inside the pool. The factory owner loses the ability to adjust protocol fee rates on the affected pool because `setPoolProtocolFee` calls `collectFees` as its first step and will always revert. This constitutes a direct, irrecoverable loss of protocol fee revenue and a broken core governance function.

### Likelihood Explanation
The pool admin is a distinct, semi-trusted role — not the factory owner. Any party that deploys a pool controls the initial `admin` address and can later call `setPoolAdminFeeDestination` at any time after deployment. The attack requires a single permissioned transaction with no preconditions beyond holding the pool admin role. A malicious or compromised pool admin can execute this silently; the only on-chain signal is the `PoolAdminFeeDestinationUpdated` event, which is easy to miss before fees accumulate.

### Recommendation
Separate the admin and protocol fee transfers so that a failure in the admin leg cannot block the protocol leg. One approach is to use a `try/catch` around the admin transfer and emit an event on failure (leaving admin fees claimable via a pull pattern). Alternatively, split `collectFees` into two independent functions — one for the protocol share and one for the admin share — so the protocol can always collect its portion regardless of the admin destination's behaviour. At minimum, `setPoolAdminFeeDestination` should validate that the new address can receive both pool tokens (e.g., via a small dry-run transfer or an explicit interface check).

### Proof of Concept

1. Pool admin deploys a `RevertOnReceive` contract whose `transfer`/`transferFrom` hook reverts unconditionally.
2. Pool admin calls `factory.setPoolAdminFeeDestination(pool, address(revertOnReceive))`.
3. Swaps accumulate spread surplus and `notionalFeeToken0Scaled` / `notionalFeeToken1Scaled`.
4. Anyone calls `factory.collectPoolFees(pool)` → `pool.collectFees(...)` → `transferToken0(revertOnReceive, ...)` → **revert**. Protocol receives nothing.
5. Factory owner calls `factory.setPoolProtocolFee(pool, newFee, 0)` → same revert path → **cannot update fee rates**.
6. Fees remain locked in the pool indefinitely with no recovery mechanism available to the protocol. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L327-335)
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

**File:** metric-core/contracts/MetricOmmPool.sol (L416-430)
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
