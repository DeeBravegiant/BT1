Audit Report

## Title
Pool Admin Can Set Per-Bin Additional Fees Without Any Cap Check, Bypassing Protocol Fee Governance — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

## Summary

`setPoolBinAdditionalFees` in `MetricOmmPoolFactory.sol` forwards caller-supplied `addFeeBuyE6` and `addFeeSellE6` directly to `setBinAdditionalFees` on the pool with no upper-bound validation. Every other pool-admin fee setter enforces an explicit cap against `maxAdminSpreadFeeE6`; this path does not. The per-bin values are added directly to `baseFeeX64` in the swap hot-path, allowing a pool admin to push the effective per-bin fee above the protocol's hard-coded governance caps and extract excess fees from every swap crossing the affected bin.

## Finding Description

**Path A — `setPoolAdminFees` (capped):**

`MetricOmmPoolFactory.sol` lines 414–415 enforce:
```solidity
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
``` [1](#0-0) 

**Path B — `setPoolBinAdditionalFees` (uncapped):**

`MetricOmmPoolFactory.sol` lines 450–457 pass values through with no validation:
```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
``` [2](#0-1) 

`setBinAdditionalFees` on the pool (`MetricOmmPool.sol` lines 464–474) only validates the bin index and writes the values directly: [3](#0-2) 

These values are consumed in the swap hot-path by adding them directly to `baseFeeX64`: [4](#0-3) [5](#0-4) 

`type(uint16).max` = 65,535, so the maximum per-bin additional fee is ≈6.55%. The hard cap for the base spread fee is `HARD_MAX_SPREAD_FEE_E6 = 200_000` (20%) per component: [6](#0-5) 

The total base spread can reach 40%. Adding the uncapped per-bin fee pushes the effective per-bin fee to ≈46.55%, well above the 20% hard cap enforced everywhere else.

## Impact Explanation

Every swap crossing a bin whose `addFeeBuyE6`/`addFeeSellE6` has been set to the maximum pays an additional ≈6.55% fee on top of the already-capped base spread. This fee is taken from the swap input and credited to the bin's LP balance (minus the protocol's spread share), constituting a direct, quantifiable loss of user principal on every affected swap. The pool admin can apply this to every bin simultaneously, making the entire pool's swap surface more expensive than the protocol's governance caps permit. This is a confirmed admin-boundary break: the pool admin exceeds the fee caps the protocol explicitly enforces on all other admin fee paths.

## Likelihood Explanation

The pool admin is a semi-trusted role explicitly bounded by caps (`maxAdminSpreadFeeE6`, `maxAdminNotionalFeeE8`). The protocol's own code demonstrates the intent to cap admin fee power. A compromised or malicious pool admin — a realistic threat model for a semi-trusted role — can exploit this gap without any on-chain resistance. No timelock, no protocol-owner approval, and no factory-level guard stands between the pool admin and the uncapped `setPoolBinAdditionalFees` call. The call is immediately effective and repeatable on every bin.

## Recommendation

Add an explicit cap check in `setPoolBinAdditionalFees` analogous to the checks already present in `setPoolAdminFees`:

```solidity
function setPoolBinAdditionalFees(
    address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6
) external override nonReentrant onlyPoolAdmin(pool) {
    if (addFeeBuyE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Alternatively, define a dedicated `maxAdminBinAdditionalFeeE6` constant so the per-bin cap can be tuned independently.

## Proof of Concept

```solidity
// Pool admin sets addFeeBuyE6 to type(uint16).max on the active bin
vm.prank(poolAdmin);
factory.setPoolBinAdditionalFees(pool, activeBin, type(uint16).max, type(uint16).max);
// 65535 / 1e6 ≈ 6.55% additional fee — no revert, no cap check

// Any subsequent swap crossing that bin pays baseFee + 6.55% instead of baseFee
// baseFee can already be at HARD_MAX_SPREAD_FEE_E6 * 2 = 40%
// Effective per-bin fee: ~46.55% — far above the 20% hard cap

// Contrast: setPoolAdminFees with value > maxAdminSpreadFeeE6 reverts with AdminFeeTooHigh
// setPoolBinAdditionalFees with 65535 silently succeeds — demonstrating the missing guard
```

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L44-45)
```text
  uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;
  uint24 internal constant HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L994-1004)
```text
          (curPosInBinCache, outToken0AmountScaled, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) =
            SwapMath.buyToken0InBinSpecifiedIn(
              binState,
              curPosInBinCache,
              state,
              params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
              lowerPriceX64,
              upperPriceX64,
              params.priceLimitX64,
              spreadFeeE6
            );
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1084-1093)
```text
          (curPosInBinCache, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) = SwapMath.buyToken1InBinSpecifiedOut(
            binState,
            curPosInBinCache,
            state,
            params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6),
            lowerPriceX64,
            upperPriceX64,
            params.priceLimitX64,
            spreadFeeE6
          );
```
