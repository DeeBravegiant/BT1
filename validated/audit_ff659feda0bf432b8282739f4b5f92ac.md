Audit Report

## Title
Pool Admin Can Set Uncapped Per-Bin Additional Fees, Bypassing the Global Spread Fee Hard Cap - (File: metric-core/contracts/MetricOmmPoolFactory.sol)

## Summary
`setPoolBinAdditionalFees` forwards caller-supplied `addFeeBuyE6`/`addFeeSellE6` values directly to `MetricOmmPool.setBinAdditionalFees` with no upper-bound check, while the parallel `setPoolAdminFees` path enforces `maxAdminSpreadFeeE6`. A pool admin can set per-bin additional fees up to `uint16` max (65535, i.e. 6.5535% in E6 units) on any bin, silently exceeding the factory-owner-controlled hard cap and extracting excess fees from every trader touching that bin.

## Finding Description
`setPoolAdminFees` correctly gates global admin fees: [1](#0-0) 

But `setPoolBinAdditionalFees` performs no equivalent check: [2](#0-1) 

`MetricOmmPool.setBinAdditionalFees` only validates the bin index, not the fee magnitudes, and writes the values directly into `BinState`: [3](#0-2) 

`BinState` stores both fields as `uint16`, so the maximum settable value is 65535: [4](#0-3) 

During every swap, the per-bin additional fee is added directly to `baseFeeX64` before swap math runs, for both buy and sell directions: [5](#0-4) [6](#0-5) 

`setFeeCaps` enforces `HARD_MAX_SPREAD_FEE_E6` only on the global protocol/admin spread fee caps, not on per-bin additional fees: [7](#0-6) 

## Impact Explanation
A pool admin sets `addFeeBuyE6 = 65535` on the active bin. Every trader buying token0 through that bin pays an extra 6.5535% on top of the oracle-derived base fee. The surplus fee accrues inside the bin as LP balance, effectively transferring trader principal to LPs under admin control. This is a direct, quantifiable loss of user funds on every swap touching that bin, and constitutes an admin-boundary break: the pool admin exceeds the fee caps the factory owner established via `setFeeCaps`. This meets the "Admin-boundary break: pool admin exceeds caps" allowed impact criterion at High severity.

## Likelihood Explanation
The pool admin role is explicitly semi-trusted — the protocol caps global admin fees precisely because it does not fully trust pool admins. Any pool admin, including one that turns adversarial post-deployment, can call `setPoolBinAdditionalFees` at any time with no timelock, no prior fee collection step, and no cap guard. The call requires only `onlyPoolAdmin(pool)`, a single-address check with no additional guard. [2](#0-1) 

## Recommendation
Add a hard cap check inside `setPoolBinAdditionalFees` mirroring the pattern used for global admin fees:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6  > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Alternatively, introduce a dedicated `maxBinAdditionalFeeE6` storage variable, enforce it here, and also validate it in `_unpackAndValidateBinStates` at pool creation time.

## Proof of Concept
```
1. Factory owner deploys pool with maxAdminSpreadFeeE6 = 200_000 (20%).
2. Pool admin calls:
       factory.setPoolBinAdditionalFees(pool, 0, 65535, 65535);
   No revert — the value 65535 is never compared against maxAdminSpreadFeeE6.
3. Trader calls pool.swap(recipient, false, -1e18, ...).
   Inside the swap loop, the effective buy fee becomes:
       baseFeeX64 + mulDiv(65535, ONE_X64, 1e6)
   ≈ oracle_spread_fee + 6.5535%
4. Trader receives ~6.5% fewer tokens than the oracle price implies.
   The surplus stays in the bin, accruing to LPs under admin control.
5. The factory owner's hard cap is silently exceeded with no on-chain revert.
```

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L284-299)
```text
  function setFeeCaps(
    uint24 newMaxProtocolSpreadFeeE6,
    uint24 newMaxAdminSpreadFeeE6,
    uint24 newMaxProtocolNotionalFeeE8,
    uint24 newMaxAdminNotionalFeeE8
  ) external override onlyOwner {
    if (
      newMaxProtocolSpreadFeeE6 > HARD_MAX_SPREAD_FEE_E6 || newMaxAdminSpreadFeeE6 > HARD_MAX_SPREAD_FEE_E6
        || newMaxProtocolNotionalFeeE8 > HARD_MAX_NOTIONAL_FEE_E8 || newMaxAdminNotionalFeeE8 > HARD_MAX_NOTIONAL_FEE_E8
    ) {
      revert FeeCapsExceedHardLimit();
    }
    maxProtocolSpreadFeeE6 = newMaxProtocolSpreadFeeE6;
    maxAdminSpreadFeeE6 = newMaxAdminSpreadFeeE6;
    maxProtocolNotionalFeeE8 = newMaxProtocolNotionalFeeE8;
    maxAdminNotionalFeeE8 = newMaxAdminNotionalFeeE8;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L414-415)
```text
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L450-457)
```text
  function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L469-473)
```text
    if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
    emit BinAdditionalFeesUpdated(bin, addFeeBuyE6, addFeeSellE6);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L910-910)
```text
            params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1177-1177)
```text
              params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6),
```

**File:** metric-core/contracts/types/PoolStorage.sol (L19-25)
```text
struct BinState {
  uint104 token0BalanceScaled;
  uint104 token1BalanceScaled;
  uint16 lengthE6;
  uint16 addFeeBuyE6;
  uint16 addFeeSellE6;
}
```
