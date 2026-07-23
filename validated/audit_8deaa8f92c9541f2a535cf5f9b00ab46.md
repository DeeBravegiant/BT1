Audit Report

## Title
Pool Admin Can Set Uncapped Per-Bin Additional Fees, Bypassing the Global Spread Fee Hard Cap - (File: metric-core/contracts/MetricOmmPoolFactory.sol)

## Summary
`setPoolBinAdditionalFees` in `MetricOmmPoolFactory` forwards caller-supplied `addFeeBuyE6`/`addFeeSellE6` values directly to `MetricOmmPool.setBinAdditionalFees` with no upper-bound check, while the analogous `setPoolAdminFees` path enforces `maxAdminSpreadFeeE6`. A pool admin can set per-bin additional fees up to `uint16` max (65 535 = 6.5535% in E6 units) on any bin, silently bypassing the factory-owner-controlled hard cap and causing direct, quantifiable loss of trader principal on every swap through that bin.

## Finding Description
`setPoolAdminFees` correctly gates fee changes behind the factory-owner-controlled cap: [1](#0-0) 

But `setPoolBinAdditionalFees` passes values straight through with no cap check: [2](#0-1) 

`MetricOmmPool.setBinAdditionalFees` only validates the bin index, not the fee magnitudes: [3](#0-2) 

The `BinState` struct stores both fields as `uint16`, capping the maximum settable value at 65 535: [4](#0-3) 

During every swap, the per-bin additional fee is added directly to `baseFeeX64` before swap math runs: [5](#0-4) [6](#0-5) 

The factory-owner-controlled `HARD_MAX_SPREAD_FEE_E6` and `maxAdminSpreadFeeE6` are enforced on global spread fees via `setFeeCaps`: [7](#0-6) 

But neither constant is checked anywhere in the `setPoolBinAdditionalFees` → `setBinAdditionalFees` call path, making the per-bin fee path a complete bypass of the cap system.

## Impact Explanation
A pool admin setting `addFeeBuyE6 = 65535` on the active bin causes every trader buying token0 through that bin to pay an extra 6.5535% on top of the oracle-derived base fee. The surplus is retained inside the bin as LP balance, directly transferring trader principal to LPs under admin control. This is a direct, quantifiable loss of user funds on every swap touching that bin, and constitutes an admin-boundary break: pool admin exceeds the spread fee caps the factory owner established via `setFeeCaps`. Severity is High.

## Likelihood Explanation
The pool admin role is explicitly semi-trusted — the protocol caps global admin fees precisely because it does not fully trust pool admins. Any pool admin, including one that turns adversarial after deployment, can call `setPoolBinAdditionalFees` at any time with no timelock, no cap, and no prior fee collection step. The call requires only `onlyPoolAdmin(pool)`, a single-address check with no additional guard. [2](#0-1) 

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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L290-295)
```text
    if (
      newMaxProtocolSpreadFeeE6 > HARD_MAX_SPREAD_FEE_E6 || newMaxAdminSpreadFeeE6 > HARD_MAX_SPREAD_FEE_E6
        || newMaxProtocolNotionalFeeE8 > HARD_MAX_NOTIONAL_FEE_E8 || newMaxAdminNotionalFeeE8 > HARD_MAX_NOTIONAL_FEE_E8
    ) {
      revert FeeCapsExceedHardLimit();
    }
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
