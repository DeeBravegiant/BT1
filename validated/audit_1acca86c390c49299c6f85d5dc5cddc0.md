Audit Report

## Title
`setPoolBinAdditionalFees` Lacks Cap Validation, Allowing Pool Admin to Bypass Factory-Enforced Fee Caps — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

## Summary
`MetricOmmPoolFactory.setPoolBinAdditionalFees` forwards `addFeeBuyE6` and `addFeeSellE6` directly to the pool with no cap check, while the parallel path `setPoolAdminFees` enforces `maxAdminSpreadFeeE6`. A pool admin can set per-bin additional fees to the full `uint16` maximum (65,535 in E6 = 6.5535%) on any bin, and these fees are applied additively on top of the oracle-derived base spread fee during every swap through that bin, causing traders to pay more than the protocol's stated fee ceiling and the excess to accrue as spread surplus collectible by the admin.

## Finding Description
Two factory-gated paths modify the effective swap fee charged to traders:

**Path 1 — `setPoolAdminFees` (cap enforced):**
```solidity
// MetricOmmPoolFactory.sol L414-415
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
```

**Path 2 — `setPoolBinAdditionalFees` (no cap check):**
```solidity
// MetricOmmPoolFactory.sol L450-457
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

`MetricOmmPool.setBinAdditionalFees` performs only a bin-range check:
```solidity
// MetricOmmPool.sol L464-474
if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
BinState storage s = _binStates[bin];
s.addFeeBuyE6 = addFeeBuyE6;
s.addFeeSellE6 = addFeeSellE6;
```

During swap execution, `addFeeBuyE6`/`addFeeSellE6` are added directly on top of `baseFeeX64` (the oracle-derived spread fee) for every bin traversed:
```solidity
// MetricOmmPool.sol L999
params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6)
// MetricOmmPool.sol L1177
params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6)
```

Since `addFeeBuyE6` and `addFeeSellE6` are `uint16`, they can be set to 65,535 (6.5535% in E6) with no factory-level cap enforcement. The factory's `maxAdminSpreadFeeE6` (defaulting to `HARD_MAX_SPREAD_FEE_E6 = 200_000`, i.e. 20%) is never consulted in this path.

## Impact Explanation
Per-bin additional fees are applied on top of the oracle-derived base spread fee during swap execution. A pool admin can set `addFeeBuyE6 = 65535` on the active bin, making every swap through that bin pay an additional 6.5535% fee beyond what the `maxAdminSpreadFeeE6` cap is supposed to allow. Traders receive less output than the oracle/bin curve permits under the protocol's stated fee ceiling. The surplus accrues in the pool's balance above `binTotals`, and is distributed as spread fees collectible by the admin via `collectPoolFees`. This constitutes a direct swap conservation failure and an admin-boundary break.

## Likelihood Explanation
The pool admin is a semi-trusted role explicitly bounded by factory caps. The call path is direct and requires no special conditions: any pool admin can call `setPoolBinAdditionalFees` at any time with `addFeeBuyE6 = type(uint16).max`. No timelock, no co-signer, and no additional guard exists on this path.

## Recommendation
Add a cap check in `setPoolBinAdditionalFees` against `maxAdminSpreadFeeE6`:
```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```
Alternatively, enforce the cap inside `MetricOmmPool.setBinAdditionalFees` by reading the factory's current `maxAdminSpreadFeeE6`.

## Proof of Concept
1. Factory owner sets `maxAdminSpreadFeeE6 = 200_000` (20%).
2. Pool admin calls `factory.setPoolAdminFees(pool, 200_000, 0)` — succeeds, capped at 20%.
3. Pool admin calls `factory.setPoolBinAdditionalFees(pool, 0, 65535, 65535)` — **succeeds with no revert**.
4. During any swap through bin 0, the effective buy fee is `baseFeeX64 + mulDiv(65535, ONE_X64, 1e6)`, adding 6.5535% on top of the oracle spread.
5. The excess tokens accumulate as pool surplus above `binTotals.scaledToken0/1` and are distributed to the admin via `collectPoolFees`, bypassing the 20% cap the protocol is supposed to enforce.