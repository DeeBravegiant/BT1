### Title
Admin Fee Destination Change Without Prior Fee Collection Misdirects Accumulated Fees to New Address — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

### Summary

`setPoolAdminFeeDestination` updates `poolAdminFeeDestination[pool]` without first collecting accumulated fees, causing all spread-surplus and notional fees earned under the old destination to be transferred to the new destination on the next `collectFees` call.

### Finding Description

The Metric OMM fee system accumulates two types of admin-owed fees between collection events:

1. **Spread surplus** — the difference between the pool's token balance and `binTotals` (grows with every swap that crosses the bid/ask spread).
2. **Notional fees** — tracked explicitly in `notionalFeeToken0Scaled` / `notionalFeeToken1Scaled` (incremented per-swap when `notionalFeeE8 > 0`).

Both are realized only when `collectFees` is called. At that point, `collectFees` receives `adminFeeDestination_` as a parameter and transfers the entire admin share of both fee types to that address in one shot. [1](#0-0) 

The factory reads `poolAdminFeeDestination[pool]` and passes it to `collectFees` every time fees are swept: [2](#0-1) 

The protocol correctly guards against stale-destination issues when fee *rates* change: both `setPoolProtocolFee` and `setPoolAdminFees` call `collectFees` with the **old** destination before updating any configuration: [3](#0-2) [4](#0-3) 

However, `setPoolAdminFeeDestination` performs no such settlement: [5](#0-4) 

It simply overwrites `poolAdminFeeDestination[pool]` and emits an event. All fees that accrued while the old destination was active are now orphaned — they will be sent to the new destination on the next `collectFees` call.

### Impact Explanation

The old admin fee destination (which may be a separate treasury, revenue-sharing contract, or third-party address distinct from the pool admin key) permanently loses all admin fees accumulated since the last collection. The new destination receives tokens it did not earn. The magnitude equals the full admin share of:

- `surplus0Scaled` / `surplus1Scaled` (all spread fees since last sweep), and
- `notionalFeeToken0Scaled` / `notionalFeeToken1Scaled` (all notional fees since last sweep).

For an active pool with a large spread fee and a long interval since the last `collectPoolFees` call, this can represent a material token loss. The tokens are not lost from the pool — they are simply redirected to the wrong recipient, constituting a direct loss of owed protocol/admin fees.

### Likelihood Explanation

Low-to-medium. The trigger is a routine, legitimate admin action (updating a treasury address). Any pool admin who rotates their fee destination without manually calling `collectPoolFees` first will silently misdirect all accumulated fees. The protocol's own documentation for `setPoolAdminFeeDestination` says only "Non-zero address. Coordinate with treasury operations" — it does not warn that fees must be swept first. [6](#0-5) 

### Recommendation

Mirror the pattern already used by `setPoolProtocolFee` and `setPoolAdminFees`: collect fees at the **current** (old) destination before updating the stored address.

```solidity
function setPoolAdminFeeDestination(address pool, address newAdminFeeDestination)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
{
    if (newAdminFeeDestination == address(0)) revert InvalidAdminFeeDestination();

    // Settle all accumulated fees to the OLD destination before rotating.
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

1. Pool is deployed with `adminFeeDestination = A`. Pool admin spread fee = 5 000 E6, notional fee = 0.
2. Many swaps occur; spread surplus accumulates (e.g., 1 000 token0 owed to admin).
3. Pool admin calls `setPoolAdminFeeDestination(pool, B)` — no `collectFees` is triggered.
4. Anyone calls `collectPoolFees(pool)`. Inside, `poolAdminFeeDestination[pool]` is now `B`.
5. `collectFees` computes `spreadFee0ToAdminScaled` from the full accumulated surplus and transfers it to `B`.
6. Address `A` receives 0 tokens despite having earned the entire 1 000 token0 spread fee. Address `B` receives 1 000 token0 it did not earn. [7](#0-6) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L385-421)
```text
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

**File:** metric-core/docs/POOL_CONFIGURATION_AND_MANAGEMENT.md (L139-141)
```markdown
| **`setPoolAdminFees(pool, newAdminSpreadFeeE6, newAdminNotionalFeeE8)`** | Accrues and transfers fees using **current** stored rates, then updates **admin** spread/notional components in **`poolFeeConfig`**, and calls **`setPoolFees`** on the pool with new **totals**. | New values must be ≤ **`maxAdminSpreadFeeE6`** / **`maxAdminNotionalFeeE8`**. Changing fees triggers a **collection** first—plan timing so destination addresses and balances are expected. |
| **`setPoolAdminFeeDestination(pool, newAdminFeeDestination)`**           | Updates where the admin fee share is sent on **`collectPoolFees`**.                                                                                                                               | Non-zero address. Coordinate with treasury operations.                                                                                                                                      |
| **`setPoolBinAdditionalFees(pool, bin, addFeeBuyE6, addFeeSellE6)`**     | Updates **per-bin** additional buy/sell fees on the pool (E6).                                                                                                                                    | Use for fine-grained incentives or disincentives on specific bins; understand interaction with global spread fee.                                                                           |
```
