### Title
Pool Admin Can Set Uncapped Per-Bin Additional Fees, Bypassing the Global Admin Fee Cap - (File: metric-core/contracts/MetricOmmPoolFactory.sol)

### Summary
`setPoolBinAdditionalFees` in `MetricOmmPoolFactory.sol` passes `addFeeBuyE6`/`addFeeSellE6` directly to the pool with no upper-bound validation, while the analogous `setPoolAdminFees` enforces `maxAdminSpreadFeeE6`. Because per-bin fees are additive to the global spread fee in every swap through the affected bin, a pool admin can push the effective per-bin fee above the protocol's intended hard cap, causing traders to pay more than the capped maximum.

### Finding Description

`setPoolAdminFees` enforces a cap on the admin's global spread fee component:

```solidity
// MetricOmmPoolFactory.sol:414-415
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
``` [1](#0-0) 

`setPoolBinAdditionalFees`, by contrast, performs no such validation — it forwards the caller-supplied `uint16` values directly to the pool:

```solidity
// MetricOmmPoolFactory.sol:450-457
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
``` [2](#0-1) 

`setBinAdditionalFees` on the pool also performs no cap check — only a bin-index range check: [3](#0-2) 

In every swap path, the per-bin fee is **added** to the oracle-derived base fee before being applied to the trade:

```solidity
// MetricOmmPool.sol:910 (buy token0, exact-out)
params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6)

// MetricOmmPool.sol:999 (buy token0, exact-in)
params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6)

// MetricOmmPool.sol:1088 (buy token1, exact-out)
params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6)

// MetricOmmPool.sol:1177 (buy token1, exact-in)
params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6)
``` [4](#0-3) [5](#0-4) 

The same additive pattern is confirmed in `getSellAndBuyPrices` (the price quoter):

```solidity
// MetricOmmPool.sol:540-541
uint256 buyFeeX64 = baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6);
uint256 sellFeeX64 = baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6);
``` [6](#0-5) 

`uint16` allows values up to 65 535, which in E6 units equals **6.5535 %**. The global spread fee hard cap is `HARD_MAX_SPREAD_FEE_E6 = 200_000` (20 %). A pool admin who has already set the global admin spread fee to the maximum can additionally set per-bin fees to 65 535, making the effective fee for that bin **26.5535 %** — 6.5535 percentage points above the protocol's intended ceiling. [7](#0-6) 

The excess fee accumulates as pool surplus and is swept to admin and protocol during `collectFees`, so the pool admin directly profits from the uncapped increment. [8](#0-7) 

### Impact Explanation

Every swap routed through the affected bin pays the uncapped per-bin fee on top of the global spread fee. The extra fee is deducted from the trader's input (exact-output swaps) or reduces the trader's output (exact-input swaps), constituting a direct loss of user principal. The excess accumulates as surplus and is collected by the admin, bypassing the cap the protocol enforces on `setPoolAdminFees`. Severity: **Medium** (requires semi-trusted pool admin to act maliciously; loss is bounded by `uint16` max = 6.5535 % per bin, but applies to all volume through that bin).

### Likelihood Explanation

The pool admin is described as "semi-trusted." The attack requires no external oracle manipulation, no flash loan, and no unprivileged path — only the pool admin calling `setPoolBinAdditionalFees` with `addFeeBuyE6 = 65535` or `addFeeSellE6 = 65535`. The contest's allowed impact gate explicitly includes "pool admin exceeds caps" as in-scope.

### Recommendation

Add a cap check in `setPoolBinAdditionalFees` mirroring the check in `setPoolAdminFees`:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Alternatively, introduce a dedicated `maxAdminBinAdditionalFeeE6` cap that can be set independently of the global spread fee cap.

### Proof of Concept

1. Deploy a pool with `adminSpreadFeeE6 = maxAdminSpreadFeeE6 = 200_000` (20 %) and `protocolSpreadFeeE6 = 0`.
2. Pool admin calls `factory.setPoolBinAdditionalFees(pool, 0, 65535, 65535)`.
3. No revert occurs — `setBinAdditionalFees` only checks the bin index.
4. A trader executes a swap through bin 0. The effective fee applied is:
   - `baseFeeX64` (from oracle bid/ask) + `Math.mulDiv(65535, ONE_X64, 1e6)` ≈ 20 % global + 6.5535 % per-bin = **26.5535 %** total.
5. The trader pays 6.5535 percentage points more than the protocol's intended 20 % ceiling, with the excess accruing as surplus collected by the admin. [2](#0-1) [3](#0-2)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L43-45)
```text
  /// @dev Owner `setFeeCaps` values cannot exceed these (spread: 1e6 = 100%; notional: 1e8 = 100%)
  uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;
  uint24 internal constant HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L408-435)
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

    c.adminSpreadFeeE6 = newAdminSpreadFeeE6;
    c.adminNotionalFeeE8 = newAdminNotionalFeeE8;
    poolFeeConfig[pool] = c;

    IMetricOmmPoolFactoryActions(pool)
      .setPoolFees(c.protocolSpreadFeeE6 + c.adminSpreadFeeE6, c.protocolNotionalFeeE8 + c.adminNotionalFeeE8);
    emit PoolAdminSpreadFeeUpdated(pool, newAdminSpreadFeeE6);
    emit PoolAdminNotionalFeeUpdated(pool, newAdminNotionalFeeE8);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L449-457)
```text
  /// @inheritdoc IMetricOmmPoolFactoryPoolAdmin
  function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L385-395)
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

**File:** metric-core/contracts/MetricOmmPool.sol (L906-915)
```text
          (curPosInBinCache, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) = SwapMath.buyToken0InBinSpecifiedOut(
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
