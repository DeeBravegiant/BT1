### Title
Pool Admin Can Set Per-Bin Additional Fees Without Any Cap Enforcement, Bypassing Protocol Fee Limits — (File: `metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`setPoolBinAdditionalFees` in `MetricOmmPoolFactory` forwards `addFeeBuyE6` / `addFeeSellE6` directly to the pool with **zero cap validation**, while every other admin fee setter enforces explicit caps. A pool admin can set per-bin additional fees to the full `uint16` ceiling (65 535 ≈ 6.55 % in E6), which are **additive** to the global spread fee in swap math, pushing the effective swap fee beyond the protocol's 20 % hard ceiling and directly reducing trader token output.

---

### Finding Description

The factory enforces explicit caps on all global admin fee changes: [1](#0-0) 

```solidity
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
```

But `setPoolBinAdditionalFees` passes values straight through with **no cap check**: [2](#0-1) 

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

The pool's `setBinAdditionalFees` also performs no cap check — it only validates the bin index: [3](#0-2) 

In swap math, the per-bin additional fee is **additive** to the oracle-derived base fee: [4](#0-3) 

```solidity
params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6)
```

The same additive pattern appears in `getSellAndBuyPrices` and the periphery data provider: [5](#0-4) [6](#0-5) 

The `BinState` struct stores `addFeeBuyE6` and `addFeeSellE6` as `uint16`: [7](#0-6) 

`uint16` max = 65 535. In E6 units (1e6 = 100 %) that is **6.5535 %**. The protocol's hard cap on the global spread fee is 20 % (`HARD_MAX_SPREAD_FEE_E6 = 200_000`). Because the per-bin fee is additive, a pool admin can push the effective fee for any single bin to **≈ 26.55 %**, silently exceeding the hard ceiling the protocol enforces everywhere else.

---

### Impact Explanation

- **Swap conservation failure / bad-price execution**: traders swapping through the affected bin receive fewer tokens than the oracle price permits. The fee applied to their swap exceeds the protocol's intended maximum.
- **Admin-boundary break**: the pool admin bypasses the fee-cap system that the protocol explicitly enforces for global fees (`maxAdminSpreadFeeE6`, `HARD_MAX_SPREAD_FEE_E6`). The per-bin path has no analogous guard.
- The surplus token1 paid by the trader above the intended cap is retained by the pool as LP spread surplus — LPs gain at traders' expense beyond the protocol-sanctioned limit.

---

### Likelihood Explanation

- The pool admin is a semi-trusted role with direct, immediate access to `setPoolBinAdditionalFees` — no timelock, no governance vote, no second step.
- The call path is: `poolAdmin` → `MetricOmmPoolFactory.setPoolBinAdditionalFees` → `MetricOmmPool.setBinAdditionalFees` — three lines, no revert path for any fee value ≤ 65 535.
- The active bin (bin 0 or whichever bin the cursor sits in) is the highest-traffic target; setting its additional fee to max immediately affects all live swaps.

---

### Recommendation

Add cap validation in `setPoolBinAdditionalFees` consistent with `setPoolAdminFees`:

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    if (addFeeBuyE6  > maxAdminBinAdditionalFeeE6) revert AdminFeeTooHigh();
    if (addFeeSellE6 > maxAdminBinAdditionalFeeE6) revert AdminFeeTooHigh();
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

Alternatively, enforce that `globalSpreadFeeE6 + addFeeBuyE6 ≤ HARD_MAX_SPREAD_FEE_E6` at the time of the call, mirroring the invariant the protocol already maintains for global fees.

---

### Proof of Concept

```
1. Pool is deployed with spreadFeeE6 = 200_000 (20%, at the hard cap).
2. Pool admin calls:
       factory.setPoolBinAdditionalFees(pool, 0, 65535, 65535);
   → No revert. BinState[0].addFeeBuyE6 = 65535, addFeeSellE6 = 65535.

3. Trader calls swap (zeroForOne = false, buying token0):
   Effective buy fee applied in SwapMath.buyToken0InBinSpecifiedIn:
       feeX64 = baseFeeX64 + mulDiv(65535, ONE_X64, 1e6)
             ≈ spreadFeeX64(20%) + 6.5535%
             ≈ 26.5535% total

4. Trader pays ~26.55% above oracle mid-price instead of the protocol-capped 20%.
   Excess ~6.55% is retained in the pool as LP surplus — trader loses real token1
   beyond what the protocol's fee ceiling permits.
```

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L999-999)
```text
              params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
```

**File:** metric-periphery/contracts/lens/MetricOmmPoolDataProvider.sol (L295-296)
```text
    uint256 buyFeeX64 = baseFeeX64 + Math.mulDiv(uint256(addFeeBuyE6), Q64, ONE_E6);
    uint256 sellFeeX64 = baseFeeX64 + Math.mulDiv(uint256(addFeeSellE6), Q64, ONE_E6);
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
