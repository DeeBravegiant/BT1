Let me check the `receiveFromRewardReceiver` function and `LRTDepositPool` to confirm TVL inflation, and also verify `protocolFeeInBPS` access.

### Title
DoS on `updateRSETHPrice()` When `maxFeeMintAmountPerDay == 0` and TVL Increases — (`contracts/LRTOracle.sol`)

---

### Summary

When `maxFeeMintAmountPerDay` is zero (the default uninitialized value, or explicitly set by a manager), any call to `updateRSETHPrice()` that computes a non-zero protocol fee will revert with `DailyFeeMintLimitExceeded`. Because `FeeReceiver.sendFunds()` is permissionless, an attacker can force TVL to increase and then trigger this revert, freezing the price oracle. The impact is **temporary freezing of funds** (Medium), not direct theft (Critical as claimed in the question).

---

### Finding Description

**Root cause — `_checkAndUpdateDailyFeeMintLimit` has no guard for `maxFeeMintAmountPerDay == 0`:** [1](#0-0) 

The check at line 205 is:

```solidity
if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
    revert DailyFeeMintLimitExceeded(...);
}
```

When `maxFeeMintAmountPerDay == 0` and `feeAmount > 0`, the condition is `0 + feeAmount > 0` → always `true` → always reverts.

**Contrast with the view function**, which *does* special-case zero: [2](#0-1) 

`remainingDailyFeeMintLimit()` returns `0` immediately when `maxFeeMintAmountPerDay == 0`, implying the protocol treats zero as a valid/disabled state — but the internal enforcement function does not honour that semantics.

**`maxFeeMintAmountPerDay` is 0 by default** (uninitialized `uint256`) and can be explicitly set to 0 by a manager: [3](#0-2) 

**`FeeReceiver.sendFunds()` is permissionless** — no role check, no access control: [4](#0-3) 

**`updateRSETHPrice()` is also permissionless:** [5](#0-4) 

**Fee is computed whenever `totalETHInProtocol > previousTVL` and `protocolFeeInBPS > 0`:** [6](#0-5) 

**Fee path that calls the broken limit check:** [7](#0-6) 

---

### Impact Explanation

The price oracle (`rsETHPrice`) cannot be updated while `maxFeeMintAmountPerDay == 0` and any TVL growth exists. Downstream contracts that gate deposits or withdrawals on a fresh oracle price will be blocked. An admin can unblock by calling `setMaxFeeMintAmountPerDay` with a non-zero value, so the freeze is **temporary**, not permanent.

**Correct scope: Medium — Temporary freezing of funds.**  
The claimed scope (Critical — direct theft) does not apply; no funds are stolen or permanently lost.

---

### Likelihood Explanation

- `maxFeeMintAmountPerDay` is `0` by default before any manager sets it, making this reachable in the early deployment window or after an explicit reset to zero.
- Both trigger calls (`sendFunds()`, `updateRSETHPrice()`) require no privileges.
- `protocolFeeInBPS > 0` is the normal production configuration.
- Likelihood is **medium**: requires a specific (but realistic) configuration state.

---

### Recommendation

Add an early-exit in `_checkAndUpdateDailyFeeMintLimit` when `maxFeeMintAmountPerDay == 0`, treating zero as "fee minting disabled / no limit enforced":

```solidity
function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
    if (maxFeeMintAmountPerDay == 0) return; // fee minting disabled; skip limit check
    // ... existing logic
}
```

Alternatively, if zero is intended to mean "no fee minting allowed at all", then `_updateRsETHPrice` should skip the fee computation entirely when `maxFeeMintAmountPerDay == 0`, rather than computing a fee and then failing the limit check.

---

### Proof of Concept

```
Preconditions:
  maxFeeMintAmountPerDay = 0
  protocolFeeInBPS > 0
  FeeReceiver holds 1 ETH
  Protocol not paused

Step 1: attacker calls FeeReceiver.sendFunds()
  → 1 ETH forwarded to LRTDepositPool via receiveFromRewardReceiver
  → totalETHInProtocol increases

Step 2: attacker calls LRTOracle.updateRSETHPrice()
  → totalETHInProtocol > previousTVL
  → rewardAmount > 0
  → protocolFeeInETH = (rewardAmount * protocolFeeInBPS) / 10_000 > 0
  → rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice) > 0
  → _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee)
  → 0 + rsethAmountToMintAsProtocolFee > 0 == true
  → revert DailyFeeMintLimitExceeded

Result: rsETHPrice is never updated; oracle is frozen until admin reconfigures maxFeeMintAmountPerDay.
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
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

**File:** contracts/LRTOracle.sol (L170-172)
```text
    function remainingDailyFeeMintLimit() external view returns (uint256) {
        if (maxFeeMintAmountPerDay == 0) return 0;

```

**File:** contracts/LRTOracle.sol (L197-210)
```text
    function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal {
        // Reset the period if it's unset or a day has passed
        if (block.timestamp >= feePeriodStartTime + 1 days) {
            currentPeriodMintedFeeAmount = 0;
            feePeriodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }

        currentPeriodMintedFeeAmount += feeAmount;
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

**File:** contracts/FeeReceiver.sol (L53-58)
```text
    function sendFunds() external {
        uint256 balance = address(this).balance;
        ILRTDepositPool(depositPool).receiveFromRewardReceiver{ value: balance }();

        emit MevRewardsAddedToTVL(balance);
    }
```
