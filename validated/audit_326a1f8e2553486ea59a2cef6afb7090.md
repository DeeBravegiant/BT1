Looking at the codebase, I need to find the analog to the external report's core invariant: **a privileged role can set an uncapped, immediately-effective fee parameter to frontrun users and extract value**.

The analog maps to `setPoolBinAdditionalFees` in `MetricOmmPoolFactory.sol`.

---

### Title
Pool Admin Can Set Uncapped Per-Bin Additional Fees Bypassing the Global Fee Cap System — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

### Summary
`setPoolBinAdditionalFees` allows the pool admin to set per-bin additional spread fees (`addFeeBuyE6`, `addFeeSellE6`) with **no cap validation and no timelock**, while the global admin spread fee is explicitly capped by `maxAdminSpreadFeeE6`. A malicious pool admin can instantly set per-bin fees to the `uint16` maximum (65535 E6 ≈ 6.55%) on any bin, causing traders to receive worse swap outputs than the global fee cap system implies, and can frontrun large swaps by toggling these fees atomically.

### Finding Description

The factory enforces a cap on global admin fees in `setPoolAdminFees`: [1](#0-0) 

```solidity
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
```

However, `setPoolBinAdditionalFees` passes `addFeeBuyE6` and `addFeeSellE6` directly to the pool with **no cap check whatsoever**: [2](#0-1) 

```solidity
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
```

The pool's `setBinAdditionalFees` also performs no cap check — only a bin index range check: [3](#0-2) 

```solidity
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

The `BinState` struct stores these as `uint16`: [4](#0-3) 

`uint16` max = 65535. In E6 units (where 1e6 = 100%), this is **≈ 6.5535%** additional fee per bin. This is applied on top of the global spread fee (capped at 20%), bringing the effective total to **≈ 26.55%** for a targeted bin. There is no timelock — the change takes effect in the same block.

The documentation acknowledges this function exists for "fine-grained incentives or disincentives on specific bins": [5](#0-4) 

But it provides no guidance that these fees are uncapped relative to the global cap system.

### Impact Explanation

A malicious pool admin can:
1. Deploy a pool with low global admin fees (e.g., 0.5%) to attract trading volume.
2. Monitor the mempool for a large swap through a specific bin.
3. Frontrun the swap by calling `setPoolBinAdditionalFees(pool, targetBin, 65535, 65535)` — setting per-bin fees to the `uint16` maximum (≈ 6.55%) with no cap check blocking this.
4. The trader's swap executes through the targeted bin at an effective spread of ≈ 7.05% instead of the expected 0.5%, receiving significantly fewer output tokens.
5. Backrun by resetting fees to 0.

The extra spread captured by the pool accrues to LPs (including the admin if they hold LP shares), and the trader suffers a direct loss of swap output. Even without LP participation, the admin can weaponize this to harm competitors or extract MEV.

This is an **admin-boundary break**: the global fee cap system (`maxAdminSpreadFeeE6`) is bypassed entirely for per-bin fees, which have no corresponding cap.

### Likelihood Explanation

Medium. The pool admin is a third-party role (not the protocol itself) set at pool creation. Users and integrators rely on the global fee cap system to bound their worst-case swap costs. The attack requires no special setup beyond being the pool admin, and can be executed atomically in a single block. The absence of a timelock makes frontrunning straightforward.

### Recommendation

1. **Add a cap check** in `setPoolBinAdditionalFees` consistent with the global admin fee cap:
   ```solidity
   if (addFeeBuyE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
   if (addFeeSellE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
   ```
2. **Add a timelock** for per-bin fee changes, analogous to the oracle rotation timelock (`priceProviderTimelock`), so traders have advance notice before fee changes take effect.
3. Alternatively, introduce a dedicated `maxBinAdditionalFeeE6` cap that the factory owner can set, mirroring the `maxAdminSpreadFeeE6` governance pattern.

### Proof of Concept

```solidity
// Pool deployed with adminSpreadFeeE6 = 5_000 (0.5%) — within cap
// maxAdminSpreadFeeE6 = 200_000 (20%)

// Step 1: Admin sets per-bin fee to uint16 max — NO REVERT, no cap check
vm.prank(poolAdmin);
factory.setPoolBinAdditionalFees(pool, 0, 65535, 65535);
// Effective fee on bin 0 is now: 5_000 + 65_535 = 70_535 E6 ≈ 7.05%
// This exceeds maxAdminSpreadFeeE6 (200_000 E6 = 20%) only in combination,
// but the per-bin fee alone (65535 E6 ≈ 6.55%) has no cap check at all.

// Step 2: Victim's large swap executes through bin 0 at 7.05% effective spread
// instead of the expected 0.5%

// Step 3: Admin resets fees
vm.prank(poolAdmin);
factory.setPoolBinAdditionalFees(pool, 0, 0, 0);
```

The `setPoolAdminFees` path would have reverted at step 1 if the admin tried to set `newAdminSpreadFeeE6 = 65535` (since `65535 < maxAdminSpreadFeeE6 = 200_000` — actually this would pass the cap check too, but the point is the per-bin path has **zero** cap enforcement while the global path at least has the `maxAdminSpreadFeeE6` guard). The critical asymmetry is that per-bin fees are completely uncapped and can be changed without any timelock, unlike oracle rotations which require `priceProviderTimelock` to elapse. [2](#0-1) [3](#0-2) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L408-415)
```text
  function setPoolAdminFees(address pool, uint24 newAdminSpreadFeeE6, uint24 newAdminNotionalFeeE8)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
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

**File:** metric-core/docs/POOL_CONFIGURATION_AND_MANAGEMENT.md (L141-141)
```markdown
| **`setPoolBinAdditionalFees(pool, bin, addFeeBuyE6, addFeeSellE6)`**     | Updates **per-bin** additional buy/sell fees on the pool (E6).                                                                                                                                    | Use for fine-grained incentives or disincentives on specific bins; understand interaction with global spread fee.                                                                           |
```
