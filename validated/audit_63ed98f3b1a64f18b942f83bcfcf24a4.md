Audit Report

## Title
Pool Admin Bypasses `maxAdminSpreadFeeE6` Cap via `setPoolBinAdditionalFees` - (`metric-core/contracts/MetricOmmPoolFactory.sol`)

## Summary
`setPoolBinAdditionalFees` in `MetricOmmPoolFactory` forwards `addFeeBuyE6`/`addFeeSellE6` directly to the pool without validating against `maxAdminSpreadFeeE6`, while the parallel path `setPoolAdminFees` enforces that cap. Because bin additional fees are additive on top of the base spread fee in every swap execution path, a pool admin can impose up to 6.5535% per-bin fees regardless of the factory owner's configured cap, including when the cap is set to zero.

## Finding Description
`MetricOmmPoolFactory` exposes two admin fee-setting paths:

**Path A — validated** (`setPoolAdminFees`, lines 414–415):
```solidity
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
``` [1](#0-0) 

**Path B — unvalidated** (`setPoolBinAdditionalFees`, lines 450–457):
```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
``` [2](#0-1) 

The pool's `setBinAdditionalFees` performs no fee-value validation beyond bin index bounds: [3](#0-2) 

In every swap execution path, `addFeeBuyE6`/`addFeeSellE6` are added directly on top of `baseFeeX64`: [4](#0-3) [5](#0-4) 

The same additive pattern appears in `getSellAndBuyPrices`: [6](#0-5) 

`BinState` stores `addFeeBuyE6`/`addFeeSellE6` as `uint16`, capping raw values at 65 535 (= 6.5535% at E6 scale): [7](#0-6) 

The factory hard-caps the base admin spread fee at `HARD_MAX_SPREAD_FEE_E6 = 200_000` (20%), but no equivalent hard cap or soft cap exists for bin additional fees: [8](#0-7) 

`maxAdminSpreadFeeE6` is a public state variable settable by the factory owner to any value including zero: [9](#0-8) 

## Impact Explanation
Users swapping through a bin with elevated `addFeeBuyE6`/`addFeeSellE6` pay higher fees than the factory-owner-enforced ceiling permits. The bin additional fees are additive on top of the oracle-derived spread fee and are never counted against `maxAdminSpreadFeeE6`. If the factory owner sets `maxAdminSpreadFeeE6 = 0` to restrict admin fees entirely, the pool admin can still impose up to 6.5535% per bin through this path with no timelock and no cap check. This constitutes a direct, measurable loss of user principal above Sherlock Medium thresholds and satisfies the "Admin-boundary break: pool admin exceeds caps" allowed impact gate.

## Likelihood Explanation
The pool admin is a semi-trusted role explicitly constrained by factory fee caps. The bypass requires only a single `setPoolBinAdditionalFees` call with no special setup, no flash loan, and no oracle manipulation. Any pool admin (malicious or compromised) can trigger it immediately and repeatedly without a timelock.

## Recommendation
Add the same cap guard in `setPoolBinAdditionalFees` that exists in `setPoolAdminFees`:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6  > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Alternatively, introduce a dedicated `HARD_MAX_BIN_ADDITIONAL_FEE_E6` constant and validate against it, mirroring the two-layer (hard limit → soft cap) pattern already used for spread and notional fees.

## Proof of Concept
1. Factory owner deploys a pool and sets `maxAdminSpreadFeeE6 = 0` via `setFeeCaps(0, 0, …)` to prevent any admin spread fee.
2. Pool admin calls `factory.setPoolBinAdditionalFees(pool, 0, 65535, 65535)`.
3. No revert occurs — `addFeeBuyE6 = addFeeSellE6 = 65535` is written to bin 0 in `_binStates`.
4. A user swaps through bin 0; the swap math computes `baseFeeX64 + Math.mulDiv(65535, ONE_X64, 1e6)`, charging an additional ~6.55% fee on top of the oracle spread.
5. The excess fee accrues to the pool and is later collected by the admin, bypassing the factory owner's intended zero-fee policy.

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L44-45)
```text
  uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;
  uint24 internal constant HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L62-62)
```text
  uint24 public override maxAdminSpreadFeeE6;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L464-474)
```text
  function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    onlyFactory
    nonReentrant(PoolActions.SET_BIN_ADDITIONAL_FEES)
  {
    if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
    emit BinAdditionalFeesUpdated(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L540-541)
```text
    uint256 buyFeeX64 = baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6);
    uint256 sellFeeX64 = baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L910-910)
```text
            params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
```

**File:** metric-core/contracts/MetricOmmPool.sol (L999-999)
```text
              params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
```

**File:** metric-core/contracts/types/PoolStorage.sol (L17-24)
```text
/// @param addFeeBuyE6 Additional fee for buying token0; 1e6 = 100%
/// @param addFeeSellE6 Additional fee for buying token1; 1e6 = 100%
struct BinState {
  uint104 token0BalanceScaled;
  uint104 token1BalanceScaled;
  uint16 lengthE6;
  uint16 addFeeBuyE6;
  uint16 addFeeSellE6;
```
