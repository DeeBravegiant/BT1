### Title
`_checkAndUpdateDailyFeeMintLimit` Reverts on Every `updateRSETHPrice()` Call When `maxFeeMintAmountPerDay = 0` and Protocol Fee Is Non-Zero — (`contracts/LRTOracle.sol`)

---

### Summary

When `maxFeeMintAmountPerDay` is set to `0` (a valid, unvalidated admin configuration) and `protocolFeeInBPS > 0`, any TVL increase causes `_updateRsETHPrice()` to compute a non-zero `rsethAmountToMintAsProtocolFee` and then call `_checkAndUpdateDailyFeeMintLimit(feeAmount)`. Because `0 + feeAmount > 0` is always `true`, the function reverts with `DailyFeeMintLimitExceeded` on every invocation — including `updateRSETHPriceAsManager()` — making the price-update mechanism permanently broken until the admin reconfigures `maxFeeMintAmountPerDay`.

---

### Finding Description

**Root cause — `_checkAndUpdateDailyFeeMintLimit` has no zero-limit bypass:** [1](#0-0) 

The guard at line 205 is:
```solidity
if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
    revert DailyFeeMintLimitExceeded(...);
}
```
When `maxFeeMintAmountPerDay = 0` and `feeAmount > 0`, this is `0 + feeAmount > 0` → always `true` → always reverts.

**`setMaxFeeMintAmountPerDay` accepts `0` without validation:** [2](#0-1) 

No lower-bound check exists. Setting `0` is a legitimate manager action (e.g., to temporarily disable fee minting).

**`remainingDailyFeeMintLimit()` explicitly handles `0` as a valid state:** [3](#0-2) 

The view function returns `0` when `maxFeeMintAmountPerDay == 0`, confirming the protocol treats `0` as a valid configuration — but the internal write path does not mirror this handling.

**The fee computation path that triggers the revert:** [4](#0-3) 

When `totalETHInProtocol > previousTVL` and `protocolFeeInBPS > 0`, `protocolFeeInETH` is non-zero. [5](#0-4) 

`_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee)` is called with a non-zero argument, triggering the revert.

**`OneETHPriceOracle` always returns `1e18`:** [6](#0-5) 

This means any new deposit increases `totalETHInProtocol` by exactly the deposited amount, reliably producing `totalETHInProtocol > previousTVL` on the next price update call.

**Both public entry points are blocked:** [7](#0-6) 

Both `updateRSETHPrice()` (public) and `updateRSETHPriceAsManager()` (manager-only) call `_updateRsETHPrice()`, so neither can succeed.

**Deposits use the stale `rsETHPrice` directly:** [8](#0-7) 

`getRsETHAmountToMint` reads `lrtOracle.rsETHPrice()` — the stored stale value — so depositors receive rsETH at an incorrect (stale) exchange rate for the entire blocked period.

---

### Impact Explanation

- `updateRSETHPrice()` reverts for every caller (public and manager) as long as `maxFeeMintAmountPerDay = 0` and TVL keeps increasing.
- The period does not self-heal: after 24 hours `currentPeriodMintedFeeAmount` resets to `0`, but the same condition (`0 + feeAmount > 0`) triggers again immediately on the next call.
- `rsETHPrice` becomes permanently stale until the admin sets `maxFeeMintAmountPerDay` to a non-zero value.
- Depositors mint rsETH at the stale (under-valued) price, receiving more rsETH than they should — a direct loss to existing holders.
- Any withdrawal mechanism that depends on a fresh price update is also blocked.

**Impact: Medium — Temporary freezing of funds / stale price causing incorrect minting.**

---

### Likelihood Explanation

- `maxFeeMintAmountPerDay = 0` is the **default state** after `initialize()` (no value is set in the initializer).
- An admin may intentionally set it to `0` to "disable" fee minting, unaware it also breaks price updates.
- `protocolFeeInBPS > 0` is the normal operating state.
- Any deposit after the last price update causes `totalETHInProtocol > previousTVL`, which is routine.
- All three conditions co-exist naturally in normal protocol operation.

---

### Recommendation

Add a zero-limit bypass in `_checkAndUpdateDailyFeeMintLimit`:

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

Alternatively, treat `maxFeeMintAmountPerDay = 0` as "unlimited" and document it clearly, or add a validation in `setMaxFeeMintAmountPerDay` that prevents `0` when `protocolFeeInBPS > 0`.

---

### Proof of Concept

```solidity
// Setup:
// - maxFeeMintAmountPerDay = 0 (default or explicitly set)
// - protocolFeeInBPS = 100 (1%)
// - rsETH totalSupply > 0, rsETHPrice = 1e18
// - Asset uses OneETHPriceOracle (returns 1e18)
// - Deposit 1 ETH → totalETHInProtocol increases by 1e18

// Call:
vm.expectRevert(
    abi.encodeWithSelector(
        LRTOracle.DailyFeeMintLimitExceeded.selector,
        /* feeAmount */ (1e18 * 100) / 10_000 / 1e18, // rsethAmountToMintAsProtocolFee
        /* maxFeeMintAmountPerDay */ 0
    )
);
lrtOracle.updateRSETHPrice();

// Assert rsETHPrice is unchanged (stale):
assertEq(lrtOracle.rsETHPrice(), previousPrice);

// Assert manager call also fails:
vm.prank(manager);
vm.expectRevert(...);
lrtOracle.updateRSETHPriceAsManager();
```

### Citations

**File:** contracts/LRTOracle.sol (L87-96)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }

    /// @dev update rsETH price as an manager account
    /// @dev main benefit is to be able to update the price in case of the price going above the threshold
    /// @dev only LRT manager is allowed to call this function
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

**File:** contracts/LRTOracle.sol (L170-181)
```text
    function remainingDailyFeeMintLimit() external view returns (uint256) {
        if (maxFeeMintAmountPerDay == 0) return 0;

        // If we're on a new day but no mint has occurred yet, treat currentPeriodMintedAmount as 0
        uint256 effectiveDailyFeeMintAmount =
            (block.timestamp >= feePeriodStartTime + 1 days) ? 0 : currentPeriodMintedFeeAmount;

        return
            maxFeeMintAmountPerDay > effectiveDailyFeeMintAmount
                ? maxFeeMintAmountPerDay - effectiveDailyFeeMintAmount
                : 0;
    }
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

**File:** contracts/LRTOracle.sol (L243-247)
```text
        uint256 protocolFeeInETH = 0;
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

**File:** contracts/oracles/OneETHPriceOracle.sol (L10-12)
```text
    function getAssetPrice(address) external pure returns (uint256) {
        return 1e18;
    }
```

**File:** contracts/LRTDepositPool.sol (L516-520)
```text
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
