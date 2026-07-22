### Title
Pending Admin Fees Redirected to New Destination When `setPoolAdminFeeDestination` Is Called Without Prior Fee Collection - (File: metric-core/contracts/MetricOmmPoolFactory.sol)

### Summary

`setPoolAdminFeeDestination` updates `poolAdminFeeDestination[pool]` without first calling `collectFees`, causing all previously accrued-but-uncollected admin fees (spread surplus and notional accumulator) to be swept to the new destination instead of the old one.

### Finding Description

`MetricOmmPoolFactory` maintains two types of accrued admin fees on each pool:

1. **Spread fees** — the surplus balance above `binTotals.scaledToken0/1` that accumulates as swaps execute.
2. **Notional fees** — stored in `notionalFeeToken0Scaled` / `notionalFeeToken1Scaled` on the pool.

Both are only realized when `collectFees` is called, at which point the `adminFeeDestination_` argument determines where the admin share is transferred. [1](#0-0) 

The factory's `setPoolAdminFees` and `setPoolProtocolFee` both flush pending fees at the **old** parameters before applying any change: [2](#0-1) [3](#0-2) 

`setPoolAdminFeeDestination`, however, simply overwrites the stored destination with no prior collection: [4](#0-3) 

After the update, the next call to `collectPoolFees` (or any fee-changing function) will pass the **new** destination to `collectFees`, redirecting all fees that accrued under the old destination. [5](#0-4) 

### Impact Explanation

All admin-share tokens (token0 and token1) that accrued since the last collection are transferred to the new destination. The old destination — which may be a separate treasury, DAO, or multisig — receives nothing for the period it was the configured recipient. This is a direct, real token balance loss for the old fee destination, proportional to the volume traded since the last `collectPoolFees` call.

### Likelihood Explanation

The pool admin is a semi-trusted role that legitimately calls `setPoolAdminFeeDestination`. The trigger requires no special conditions: any pool with non-zero accrued fees (i.e., any active pool) is vulnerable the moment the admin updates the destination. The inconsistency with `setPoolAdminFees` and `setPoolProtocolFee` — which both collect first — makes accidental omission likely even without malicious intent.

### Recommendation

Mirror the pattern used by `setPoolAdminFees` and `setPoolProtocolFee`: collect pending fees at the **current** destination before overwriting it.

```solidity
function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (newAdminFeeDestination == address(0)) revert InvalidAdminFeeDestination();

    // Flush accrued fees to the OLD destination before switching
    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool).collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]   // old destination
    );

    poolAdminFeeDestination[pool] = newAdminFeeDestination;
    emit PoolAdminFeeDestinationUpdated(pool, newAdminFeeDestination);
}
```

### Proof of Concept

1. Pool is active; swaps have generated a spread surplus and a non-zero `notionalFeeToken0Scaled`.
2. Pool admin calls `setPoolAdminFeeDestination(pool, attackerWallet)`.
   - `poolAdminFeeDestination[pool]` is overwritten; no fees are collected.
3. Anyone calls `collectPoolFees(pool)`.
   - Factory reads `poolAdminFeeDestination[pool]` → `attackerWallet`.
   - `collectFees` transfers the entire admin share (spread + notional) to `attackerWallet`.
   - The original treasury address receives zero tokens for the entire accrual period. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L382-433)
```text
    uint256 notionalFee0AmountScaled = notionalFeeToken0Scaled;
    uint256 notionalFee1AmountScaled = notionalFeeToken1Scaled;

    uint256 surplus0Scaled =
      balance0() * TOKEN_0_SCALE_MULTIPLIER - uint256(binTotals.scaledToken0) - notionalFee0AmountScaled;
    uint256 surplus1Scaled =
      balance1() * TOKEN_1_SCALE_MULTIPLIER - uint256(binTotals.scaledToken1) - notionalFee1AmountScaled;

    unchecked {
      uint256 spreadFee0ToAdminScaled = spreadSumE6 == 0 ? 0 : (surplus0Scaled * adminSpreadFeeE6_) / spreadSumE6;
      uint256 spreadFee1ToAdminScaled = spreadSumE6 == 0 ? 0 : (surplus1Scaled * adminSpreadFeeE6_) / spreadSumE6;

      uint256 spreadFee0ToProtocolScaled = spreadSumE6 == 0 ? 0 : (surplus0Scaled * protocolSpreadFeeE6_) / spreadSumE6;
      uint256 spreadFee1ToProtocolScaled = spreadSumE6 == 0 ? 0 : (surplus1Scaled * protocolSpreadFeeE6_) / spreadSumE6;

      uint256 notionalFee0ToAdminScaled =
        notionalSumE8 == 0 ? 0 : (notionalFee0AmountScaled * adminNotionalFeeE8_) / notionalSumE8;
      uint256 notionalFee1ToAdminScaled =
        notionalSumE8 == 0 ? 0 : (notionalFee1AmountScaled * adminNotionalFeeE8_) / notionalSumE8;

      uint256 notionalFee0ToProtocolScaled = notionalFee0AmountScaled - notionalFee0ToAdminScaled;
      uint256 notionalFee1ToProtocolScaled = notionalFee1AmountScaled - notionalFee1ToAdminScaled;

      uint256 totalFee0ToAdminScaled = spreadFee0ToAdminScaled + notionalFee0ToAdminScaled;
      uint256 totalFee1ToAdminScaled = spreadFee1ToAdminScaled + notionalFee1ToAdminScaled;

      uint256 totalFee0ToProtocolScaled = spreadFee0ToProtocolScaled + notionalFee0ToProtocolScaled;
      uint256 totalFee1ToProtocolScaled = spreadFee1ToProtocolScaled + notionalFee1ToProtocolScaled;

      (uint256 totalFee0ToAdmin, uint256 totalFee1ToAdmin) =
        deltasScaledToExternal(totalFee0ToAdminScaled, totalFee1ToAdminScaled, Math.Rounding.Floor);
      (uint256 totalFee0ToProtocol, uint256 totalFee1ToProtocol) =
        deltasScaledToExternal(totalFee0ToProtocolScaled, totalFee1ToProtocolScaled, Math.Rounding.Floor);

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

      emit ProtocolFeesCollected(totalFee0ToProtocol, totalFee1ToProtocol, totalFee0ToAdmin, totalFee1ToAdmin);
    }
```

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
