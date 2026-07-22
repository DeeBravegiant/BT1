### Title
No Upper Bound on Per-Bin Additional Fees Allows Pool Admin to Bypass Global Spread Fee Cap - (File: `metric-core/contracts/MetricOmmPoolFactory.sol`, `metric-core/contracts/MetricOmmPool.sol`)

### Summary

`setPoolBinAdditionalFees` in `MetricOmmPoolFactory` and `setBinAdditionalFees` in `MetricOmmPool` accept `addFeeBuyE6` and `addFeeSellE6` as raw `uint16` values with no upper-bound validation. The global spread fee is hard-capped at 20% (`200_000` E6) by the factory, but the per-bin additional fee layer has no analogous cap, allowing a pool admin to set per-bin fees up to `65_535` E6 (≈6.55%) on top of the global fee, bypassing the intended protocol ceiling.

### Finding Description

The factory enforces strict caps on the global spread fee: [1](#0-0) 

```
uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;   // 20%
uint24 internal constant HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000;
```

`setPoolAdminFees` and `setPoolProtocolFee` both enforce these caps before writing: [2](#0-1) 

However, `setPoolBinAdditionalFees` passes the values straight through with **no validation**: [3](#0-2) 

And `setBinAdditionalFees` on the pool only validates the bin index, not the fee magnitudes: [4](#0-3) 

These per-bin fees are then added directly to `baseFeeX64` in every swap path: [5](#0-4) 

```solidity
params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
```

The same pattern appears for the sell direction: [6](#0-5) [7](#0-6) 

The `getSellAndBuyPrices` view confirms the same uncapped addition: [8](#0-7) 

### Impact Explanation

A pool admin sets `addFeeBuyE6 = 65_535` (max `uint16`) on the active bin. Every swap buying token0 in that bin pays:

```
effectiveFee = baseFeeX64 + (65_535 / 1_000_000) * ONE_X64
             = baseFeeX64 + 6.5535% in Q64.64
```

Combined with the maximum allowed global spread fee of 20%, the effective per-bin fee reaches **≈26.55%**, a 33% overshoot of the protocol's hard cap. The excess fee is collected as LP/protocol revenue from traders who receive a worse execution price than the protocol intends to permit. This is a direct bad-price execution and an admin-boundary break: the pool admin exceeds the fee cap the factory is designed to enforce.

The same uncapped path exists at pool creation time, since `_unpackAndValidateBinStates` validates bin distances but not the `addFeeBuyE6`/`addFeeSellE6` fields packed into each bin word. [9](#0-8) 

### Likelihood Explanation

The pool admin is a semi-trusted role (not the fully-trusted factory owner). The allowed-impact gate explicitly includes "pool admin exceeds caps" as a valid finding. The call path is permissionless for the pool admin and requires no special preconditions beyond holding the `poolAdmin[pool]` role. No existing guard in the factory or pool prevents setting `addFeeBuyE6`/`addFeeSellE6` to `type(uint16).max`.

### Recommendation

Add an upper-bound check in `setPoolBinAdditionalFees` (factory) and in `setBinAdditionalFees` (pool), mirroring the existing pattern for global fees:

```solidity
// In MetricOmmPoolFactory.setPoolBinAdditionalFees:
uint16 constant MAX_BIN_ADD_FEE_E6 = 200_000; // align with HARD_MAX_SPREAD_FEE_E6
if (addFeeBuyE6 > MAX_BIN_ADD_FEE_E6 || addFeeSellE6 > MAX_BIN_ADD_FEE_E6)
    revert BinAdditionalFeeTooHigh();
```

Alternatively, enforce the cap at the pool level inside `setBinAdditionalFees` so the invariant holds regardless of the caller.

### Proof of Concept

```solidity
// Pool admin sets per-bin fee to uint16 max on the active bin
factory.setPoolBinAdditionalFees(pool, 0, 65_535, 65_535);

// Trader swaps; effective buy fee = baseFee + 6.5535%
// With global spread fee at 20%, total effective fee ≈ 26.55%
// Trader receives fewer tokens than the 20% cap would allow
(int256 a0, int256 a1) = pool.swap(
    recipient, true, int256(1e18), type(uint128).max, ""
);
// a0 reflects ~26.55% fee instead of the capped 20%
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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L567-570)
```text
  function _unpackAndValidateBinStates(
    int24 curBinDistFromProvidedPriceE6,
    uint256[] calldata nonNegativeBinDataArray,
    uint256[] calldata negativeBinDataArray
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

**File:** metric-core/contracts/MetricOmmPool.sol (L999-1003)
```text
              params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
              lowerPriceX64,
              upperPriceX64,
              params.priceLimitX64,
              spreadFeeE6
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1088-1088)
```text
            params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6),
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1177-1177)
```text
              params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6),
```
