### Title
Daily Fee Mint Limit Revert Permanently Blocks `updateRSETHPrice()` Until Reset - (File: contracts/LRTOracle.sol)

### Summary

`LRTOracle._checkAndUpdateDailyFeeMintLimit` uses a hard revert when the daily fee mint cap is reached. Because this check is embedded inside `_updateRsETHPrice()`, once the cap is hit, **every subsequent call to `updateRSETHPrice()` and `updateRSETHPriceAsManager()` reverts for the remainder of the day**, freezing the rsETH price and blocking protocol fee accrual until the period resets.

### Finding Description

`_updateRsETHPrice()` computes a protocol fee in rsETH and passes it to `_checkAndUpdateDailyFeeMintLimit`: [1](#0-0) 

Inside `_checkAndUpdateDailyFeeMintLimit`, if the accumulated fee for the current period plus the new fee exceeds `maxFeeMintAmountPerDay`, the function reverts unconditionally: [2](#0-1) 

This revert propagates up through `_updateRsETHPrice()`, causing both public entry points to revert: [3](#0-2) 

The consequence is that once `currentPeriodMintedFeeAmount` reaches `maxFeeMintAmountPerDay` within a day, **no further price updates are possible** until the 24-hour period resets. Critically, `updateRSETHPriceAsManager()` also calls `_updateRsETHPrice()` and is equally blocked — there is no privileged bypass. [4](#0-3) 

### Impact Explanation

While the daily limit resets after 24 hours, the window of impact is significant:

1. **Stale rsETH price**: `rsETHPrice` and `highestRsethPrice` are not updated. Any protocol component reading `rsETHPrice` (deposits, withdrawals, cross-chain rate pushes) operates on a stale value.
2. **Unclaimed yield frozen**: Protocol fee rsETH that would have been minted to the treasury for the remainder of the day is permanently lost — it cannot be retroactively minted after the period resets.
3. **No privileged escape**: Even `updateRSETHPriceAsManager()` is blocked, so the manager cannot force a price update during the blocked window.

Impact classification: **Medium — Temporary freezing of funds / Permanent freezing of unclaimed yield** for the blocked period.

### Likelihood Explanation

The daily fee mint limit (`maxFeeMintAmountPerDay`) is set by the manager. If the protocol TVL grows rapidly in a single day (e.g., large deposits, staking rewards accruing), the computed `rsethAmountToMintAsProtocolFee` across multiple `updateRSETHPrice()` calls within the same 24-hour window can cumulatively exceed the cap. This is a normal operational scenario, not an attack. The likelihood increases as TVL grows.

### Recommendation

Mirror the fix applied to the Minter.sol nudge: instead of reverting when the cap is exceeded, **cap the fee at the remaining daily allowance and continue updating the price**:

```solidity
function _checkAndUpdateDailyFeeMintLimit(uint256 feeAmount) internal returns (uint256 cappedFee) {
    if (block.timestamp >= feePeriodStartTime + 1 days) {
        currentPeriodMintedFeeAmount = 0;
        feePeriodStartTime = getCurrentPeriodStartTime();
    }
    uint256 remaining = maxFeeMintAmountPerDay > currentPeriodMintedFeeAmount
        ? maxFeeMintAmountPerDay - currentPeriodMintedFeeAmount
        : 0;
    cappedFee = feeAmount > remaining ? remaining : feeAmount;
    currentPeriodMintedFeeAmount += cappedFee;
}
```

Then use `cappedFee` in `_updateRsETHPrice()` for the actual mint, allowing the price update to always proceed regardless of the fee cap state.

### Proof of Concept

1. `maxFeeMintAmountPerDay` is set to `X` rsETH.
2. During a high-yield day, `updateRSETHPrice()` is called multiple times. After several calls, `currentPeriodMintedFeeAmount` reaches `X`.
3. TVL continues to grow. The next call to `updateRSETHPrice()` computes `rsethAmountToMintAsProtocolFee > 0`.
4. `_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee)` hits line 205: `currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay` → reverts with `DailyFeeMintLimitExceeded`.
5. The revert propagates: `rsETHPrice` is **not updated**, `highestRsethPrice` is **not updated**, fee is **not minted**.
6. Every subsequent call to `updateRSETHPrice()` or `updateRSETHPriceAsManager()` reverts identically until `block.timestamp >= feePeriodStartTime + 1 days`.
7. For the remainder of the day, the protocol operates on a stale rsETH price and the treasury receives no fee for that period's yield. [4](#0-3) [5](#0-4)

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

**File:** contracts/LRTOracle.sol (L298-316)
```text
        // mint protocol fee as rsETH if there's a fee to take
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
        } else {
            _checkAndUpdateDailyFeeMintLimit(0);
        }

        rsETHPrice = newRsETHPrice;

        emit RsETHPriceUpdate(rsETHPrice, previousPrice);
    }
```
