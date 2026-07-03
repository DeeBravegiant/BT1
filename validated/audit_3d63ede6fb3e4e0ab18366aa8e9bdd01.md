The code confirms this is a real vulnerability. Here is the full analysis:

---

### Title
`maxFeeMintAmountPerDay = 0` Causes `updateRSETHPrice()` to Revert on Any TVL Increase, Freezing the Price Oracle — (`contracts/LRTOracle.sol`)

---

### Summary

When `maxFeeMintAmountPerDay` is set to `0` by the LRT Manager and staking rewards cause `totalETHInProtocol > previousTVL`, every call to `updateRSETHPrice()` reverts with `DailyFeeMintLimitExceeded`. The `rsETHPrice` state variable is never updated, leaving the oracle stale. Downstream, `LRTWithdrawalManager.unlockQueue()` reads the stale price and processes withdrawals at an outdated exchange rate.

---

### Finding Description

`setMaxFeeMintAmountPerDay(0)` is a valid, unguarded manager action: [1](#0-0) 

The view function `remainingDailyFeeMintLimit()` explicitly treats `maxFeeMintAmountPerDay == 0` as a valid state and returns `0` gracefully: [2](#0-1) 

However, `_checkAndUpdateDailyFeeMintLimit()` has no such guard. When `feeAmount > 0` and `maxFeeMintAmountPerDay == 0`, the condition `0 + feeAmount > 0` is always `true`, causing an unconditional revert: [3](#0-2) 

This revert is reached whenever `protocolFeeInETH > 0`, which happens on any TVL increase with a non-zero `protocolFeeInBPS`: [4](#0-3) 

The fee-to-rsETH conversion at line 301 produces a non-zero `rsethAmountToMintAsProtocolFee`, which is passed directly into the failing check at line 303: [5](#0-4) 

Because the revert occurs before line 313, `rsETHPrice` is never written: [6](#0-5) 

Note that `updateRSETHPriceAsManager()` also calls `_updateRsETHPrice()` and is equally affected — there is no privileged bypass path. [7](#0-6) 

The stale `rsETHPrice` is then consumed by `LRTWithdrawalManager.unlockQueue()` via `_createUnlockParams`: [8](#0-7) 

And used to compute user payouts: [9](#0-8) 

Since the actual TVL is growing (rewards accruing) but `rsETHPrice` is frozen at the old lower value, users receive less than they are owed.

---

### Impact Explanation

**Medium — Temporary freezing of funds / theft of unclaimed yield.**

- All calls to `updateRSETHPrice()` revert for as long as `maxFeeMintAmountPerDay == 0` and staking rewards are accruing.
- `rsETHPrice` becomes increasingly stale (understated relative to actual TVL).
- `unlockQueue()` uses the stale price, shortchanging withdrawing users.
- The freeze persists until the manager calls `setMaxFeeMintAmountPerDay` with a non-zero value, making it temporary but potentially lasting for an extended period.

---

### Likelihood Explanation

**Low-Medium.** The `remainingDailyFeeMintLimit()` view function explicitly handles `maxFeeMintAmountPerDay == 0` as a valid state, signaling that `0` is an intended configuration (e.g., "disable fee minting"). A manager could reasonably set it to `0` for this purpose without realizing it also blocks all price updates. No key compromise or malicious intent is required — only a plausible misconfiguration.

---

### Recommendation

Add a zero-bypass guard in `_checkAndUpdateDailyFeeMintLimit()` consistent with the existing guard in `remainingDailyFeeMintLimit()`:

```solidity
function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
    if (maxFeeMintAmountPerDay == 0) return; // fee minting disabled; skip limit check

    if (block.timestamp >= feePeriodStartTime + 1 days) {
        currentPeriodMintedFeeAmount = 0;
        feePeriodStartTime = getCurrentPeriodStartTime();
    }

    if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
        revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
    }

    currentPeriodMintedFeeAmount += feeAmount;
}
```

Alternatively, add input validation in `setMaxFeeMintAmountPerDay` to reject `0` if the intent is that `0` should never be a valid limit.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

// Pseudocode for a local fork test:
// 1. Deploy protocol with protocolFeeInBPS = 500 (5%)
// 2. Deposit assets so rsethSupply > 0 and rsETHPrice is set
// 3. Manager calls: lrtOracle.setMaxFeeMintAmountPerDay(0)
// 4. Simulate staking rewards: increase asset oracle price so
//    totalETHInProtocol > previousTVL (rsethSupply * rsETHPrice)
// 5. Call lrtOracle.updateRSETHPrice()
// 6. Assert: reverts with DailyFeeMintLimitExceeded(feeAmount, 0)
//            where feeAmount = protocolFeeInETH.divWad(newRsETHPrice) > 0
// 7. Assert: lrtOracle.rsETHPrice() == old stale value (unchanged)
// 8. Call lrtOracle.updateRSETHPriceAsManager() — also reverts
// 9. Call withdrawalManager.unlockQueue(...) — succeeds but uses stale rsETHPrice,
//    paying users less than their actual entitlement
```

### Citations

**File:** contracts/LRTOracle.sol (L94-96)
```text
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L132-135)
```text
    function setMaxFeeMintAmountPerDay(uint256 _maxFeeMintAmountPerDay) external onlyLRTManager {
        maxFeeMintAmountPerDay = _maxFeeMintAmountPerDay;
        emit MaxFeeMintAmountPerDayUpdated(_maxFeeMintAmountPerDay);
    }
```

**File:** contracts/LRTOracle.sol (L170-171)
```text
    function remainingDailyFeeMintLimit() external view returns (uint256) {
        if (maxFeeMintAmountPerDay == 0) return 0;
```

**File:** contracts/LRTOracle.sol (L205-207)
```text
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }
```

**File:** contracts/LRTOracle.sol (L244-247)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
```

**File:** contracts/LRTOracle.sol (L299-303)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

**File:** contracts/LRTWithdrawalManager.sol (L846-848)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
```
