### Title
Daily Fee Mint Limit Exhaustion Blocks rsETH Price Updates When Rewards Accrue - (File: contracts/LRTOracle.sol)

### Summary
`LRTOracle._updateRsETHPrice()` unconditionally calls `_checkAndUpdateDailyFeeMintLimit()` on every invocation. Once the daily fee mint cap (`maxFeeMintAmountPerDay`) is exhausted within a 24-hour window, any subsequent call to the public `updateRSETHPrice()` that would mint protocol fees reverts with `DailyFeeMintLimitExceeded`, leaving the on-chain rsETH price stale for the remainder of the day. The manager escape hatch `updateRSETHPriceAsManager()` is subject to the same internal call and provides no relief.

### Finding Description
Inside `_updateRsETHPrice()`, when the protocol has accrued rewards (`totalETHInProtocol > previousTVL`), a non-zero `rsethAmountToMintAsProtocolFee` is computed and passed to `_checkAndUpdateDailyFeeMintLimit()`: [1](#0-0) 

`_checkAndUpdateDailyFeeMintLimit` enforces a hard cap: [2](#0-1) 

If `currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay`, the function reverts. Because this check sits inside `_updateRsETHPrice()`, the revert propagates all the way up through both public entry points:

- `updateRSETHPrice()` — callable by anyone, gated only by `whenNotPaused`
- `updateRSETHPriceAsManager()` — callable only by the LRT manager, but still delegates to `_updateRsETHPrice()` [3](#0-2) 

Neither entry point can bypass the daily fee mint limit. The only automatic reset occurs when `block.timestamp >= feePeriodStartTime + 1 days`, meaning the price is frozen for up to 24 hours.

The stale `rsETHPrice` is then consumed by `LRTDepositPool.getRsETHAmountToMint()`: [4](#0-3) 

and by every L2 pool's `getRate()` call, which reads the oracle's stored rate for minting `wrsETH`.

### Impact Explanation
When the daily fee mint limit is exhausted while rewards are still accruing:

1. `updateRSETHPrice()` reverts for the rest of the day.
2. `rsETHPrice` in `LRTOracle` remains at its last-written value, which is lower than the true value (since new rewards have not been reflected).
3. `LRTDepositPool` uses this stale (lower) price to compute `rsethAmountToMint`, causing new depositors to receive **more rsETH than they are entitled to**, diluting existing holders.
4. L2 pools (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, etc.) use the stale rate from the oracle for all `deposit()` calls, minting `wrsETH` at an incorrect ratio.

This constitutes **temporary freezing of the price-update mechanism** and **share/asset mis-accounting** for all deposits made while the price is stale.

Impact: **Medium — Temporary freezing of funds / oracle rate abuse leading to incorrect rsETH minting.**

### Likelihood Explanation
- `maxFeeMintAmountPerDay` is a manager-controlled parameter with no on-chain floor relative to actual daily fee accrual.
- As protocol TVL grows, daily fee accrual grows proportionally. A conservatively set cap becomes increasingly likely to be exhausted within a single day.
- The public `updateRSETHPrice()` can be called by anyone, so the limit is consumed by normal keeper/bot activity, not just by an attacker.
- No special privileges or external conditions are required to trigger the revert — it is a natural consequence of normal protocol operation once the cap is hit.

### Recommendation
Decouple the fee-minting cap from the price-update path. When the daily fee mint limit is exhausted, the price update should proceed and simply skip the fee mint (or defer it), rather than reverting entirely. For example:

```solidity
if (protocolFeeInETH > 0) {
    uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);
    // Only mint if within daily limit; otherwise skip fee, still update price
    if (currentPeriodMintedFeeAmount + rsethAmountToMintAsProtocolFee <= maxFeeMintAmountPerDay) {
        _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
        IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
        emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
    }
}
rsETHPrice = newRsETHPrice; // always update price
```

### Proof of Concept
1. Admin sets `maxFeeMintAmountPerDay = 100e18` (100 rsETH/day).
2. Protocol TVL is large; each `updateRSETHPrice()` call mints ~10 rsETH in fees.
3. After 10 calls within the same 24-hour window, `currentPeriodMintedFeeAmount == 100e18`.
4. Protocol continues to accrue rewards (e.g., staking yield arrives).
5. Any user or keeper calls `updateRSETHPrice()`. Inside `_updateRsETHPrice()`, `protocolFeeInETH > 0`, so `rsethAmountToMintAsProtocolFee > 0`. `_checkAndUpdateDailyFeeMintLimit` reverts with `DailyFeeMintLimitExceeded`.
6. `updateRSETHPriceAsManager()` is called by the LRT manager — same revert, same path.
7. `rsETHPrice` remains stale (lower than true value) for up to 24 hours.
8. All `LRTDepositPool.depositETH()` / `depositAsset()` calls during this window use the stale price, minting excess rsETH to new depositors at the expense of existing holders. [5](#0-4) [1](#0-0)

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

**File:** contracts/LRTOracle.sol (L244-266)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```

**File:** contracts/LRTOracle.sol (L299-311)
```text
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
```

**File:** contracts/LRTDepositPool.sol (L516-521)
```text
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
