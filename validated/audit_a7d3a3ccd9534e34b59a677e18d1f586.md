Looking at the key contracts to trace the exact vulnerability path.

### Title
Stale `rsETHPrice` vs. Live `getExchangeRate()` Allows Depositors to Capture Accrued rETH Yield From Existing Holders — (`contracts/LRTDepositPool.sol`, `contracts/LRTOracle.sol`, `contracts/oracles/RETHPriceOracle.sol`)

---

### Summary

`LRTDepositPool.getRsETHAmountToMint()` divides a **live** rETH exchange rate by a **stale** `rsETHPrice`. Any deposit made after rETH accrues rewards but before `updateRSETHPrice()` is called mints more rsETH than the depositor's ETH contribution warrants, diluting all existing holders of the yield they had already earned.

---

### Finding Description

The mint formula in `LRTDepositPool` is:

```
rsethAmountToMint = (amount × lrtOracle.getAssetPrice(rETH)) / lrtOracle.rsETHPrice()
``` [1](#0-0) 

**Numerator — always live.**
`lrtOracle.getAssetPrice(rETH)` delegates to `RETHPriceOracle.getAssetPrice()`, which calls `IrETH(rETHAddress).getExchangeRate()` directly on the rETH token with no caching. [2](#0-1) 

**Denominator — stale until manually refreshed.**
`lrtOracle.rsETHPrice()` is a storage variable written only inside `_updateRsETHPrice()`, which is triggered by the public `updateRSETHPrice()` or the manager-only `updateRSETHPriceAsManager()`. [3](#0-2) [4](#0-3) [5](#0-4) 

rETH accrues staking rewards continuously; its exchange rate rises every Rocket Pool reward interval. `rsETHPrice` is only updated when someone calls `updateRSETHPrice()`. The gap between those two events is the attack window.

`_beforeDeposit` contains no guard that detects or prevents over-minting: [6](#0-5) 

The `minRSETHAmountExpected` parameter is depositor-side slippage protection; it cannot prevent the depositor from receiving *more* than fair value.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Concrete numeric example:

| State | rETH in protocol | rETH rate | Total ETH | rsETH supply | rsETHPrice |
|---|---|---|---|---|---|
| Before reward accrual | 100 | 1.050 | 105 ETH | 100 | 1.050 |
| After rETH rate tick (rsETHPrice not yet updated) | 100 | 1.060 | 106 ETH | 100 | **1.050 (stale)** |

Attacker deposits 100 rETH:

```
rsethAmountToMint = 100 × 1.060 / 1.050 = 100.952 rsETH   (fair: 100.000)
```

After `updateRSETHPrice()`:

```
totalETH   = 200 × 1.060 = 212 ETH
rsethSupply = 200.952
rsETHPrice  = 212 / 200.952 = 1.05497 ETH/rsETH   (fair: 1.060)
```

Original holders' 100 rsETH is now worth **105.497 ETH** instead of the **106 ETH** they earned. The attacker's 100.952 rsETH is worth **106.503 ETH** on a 106 ETH deposit — a **0.503 ETH profit extracted from existing holders**. The profit scales linearly with deposit size and with the rate delta since the last `updateRSETHPrice()` call.

The protocol is not technically insolvent (all rsETH remains backed by real ETH), so the correct impact classification is **High — Theft of unclaimed yield**, not Critical protocol insolvency as the question posits.

---

### Likelihood Explanation

- rETH exchange rate increases continuously; the window is always open between `updateRSETHPrice()` calls.
- `updateRSETHPrice()` is public but not called atomically with deposits; typical keeper cadence is hours to days.
- No special role, front-running, or oracle manipulation is required — a normal `depositAsset()` call suffices.
- Profit is proportional to deposit size, so a whale can extract meaningful yield in a single transaction.

---

### Recommendation

Enforce that `rsETHPrice` is refreshed atomically before computing the mint amount. The simplest fix is to call `_updateRsETHPrice()` (or read a freshly computed price) inside `getRsETHAmountToMint()` rather than reading the cached `rsETHPrice` storage variable. Alternatively, compute the mint ratio directly from `totalETHInProtocol / rsethSupply` on-the-fly at deposit time, bypassing the stale cache entirely.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.27;

// Fork test (Hardhat/Foundry, no public-mainnet submission)
// 1. Fork mainnet at block B where rETH.getExchangeRate() == RATE_OLD
// 2. Deploy / point to existing LRTDepositPool + LRTOracle
// 3. Call lrtOracle.updateRSETHPrice() → rsETHPrice reflects RATE_OLD
// 4. vm.mockCall(rETHAddress, getExchangeRate.selector, RATE_NEW)  // simulate reward tick
//    (RATE_NEW > RATE_OLD, e.g. +0.01 ETH)
// 5. Record attacker's rETH balance and rsETH balance before deposit
// 6. attacker.depositAsset(rETH, DEPOSIT_AMOUNT, 0, "")
// 7. Record minted rsETH
// 8. Call lrtOracle.updateRSETHPrice() → rsETHPrice now reflects RATE_NEW
// 9. Compute fair rsETH = DEPOSIT_AMOUNT * RATE_NEW / RATE_NEW = DEPOSIT_AMOUNT
// 10. Assert minted rsETH > DEPOSIT_AMOUNT  ← proves over-mint
// 11. Assert existing holders' rsETH value decreased by the delta ← proves theft of yield
```

The assertion at step 10 will pass for any `RATE_NEW > RATE_OLD` and any non-zero `DEPOSIT_AMOUNT`, confirming the vulnerability is unconditionally present whenever the rETH rate has ticked since the last `updateRSETHPrice()` call.

### Citations

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```

**File:** contracts/oracles/RETHPriceOracle.sol (L34-40)
```text
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != rETHAddress) {
            revert InvalidAsset();
        }

        return IrETH(rETHAddress).getExchangeRate();
    }
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```
