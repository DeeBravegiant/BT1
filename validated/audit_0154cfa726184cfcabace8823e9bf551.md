Looking at the actual code in `contracts/LRTOracle.sol`, I need to verify each claim precisely.

Audit Report

## Title
Daily Fee Mint Limit Exhaustion Freezes rsETH Price Updates and Enables Excess rsETH Minting for New Depositors - (File: contracts/LRTOracle.sol)

## Summary
`_updateRsETHPrice()` calls `_checkAndUpdateDailyFeeMintLimit()` unconditionally before writing `rsETHPrice`, so once `currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay`, every subsequent call reverts and the stored price is never updated. Both public entry points (`updateRSETHPrice()` and `updateRSETHPriceAsManager()`) share this path with no bypass. While the price is stale, `LRTDepositPool.getRsETHAmountToMint()` divides by the old (lower) price, minting excess rsETH to new depositors and diluting existing holders' accrued yield.

## Finding Description
Inside `_updateRsETHPrice()`, when `totalETHInProtocol > previousTVL` and the protocol is not paused, `protocolFeeInETH` is computed and `rsethAmountToMintAsProtocolFee` is derived. The call to `_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee)` at line 303 precedes the price write at line 313:

```solidity
// line 303
_checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
// ...
// line 313
rsETHPrice = newRsETHPrice;   // never reached if the check above reverts
```

`_checkAndUpdateDailyFeeMintLimit` hard-reverts when `currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay` (line 205–206). There is no code path in either `updateRSETHPrice()` (line 87–89) or `updateRSETHPriceAsManager()` (line 94–96) that skips this check. The only automatic reset is the 24-hour window at line 199. Until that window expires, every call that would mint a non-zero fee reverts, leaving `rsETHPrice` at its last-written value.

`LRTDepositPool.getRsETHAmountToMint()` reads the stale price directly:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

A stale (lower) `rsETHPrice` denominator yields a larger `rsethAmountToMint`, so every deposit made during the stale window mints excess rsETH, diluting the claims of all existing holders.

## Impact Explanation
**High — Theft of unclaimed yield.**

Existing rsETH holders have already accrued yield that is reflected in the true (higher) rsETH price. While the price is frozen at the old lower value, new depositors receive more rsETH per ETH than they are entitled to. This directly transfers value from existing holders to new depositors: the existing holders' proportional claim on the protocol's ETH is permanently reduced. The dilution is irreversible once the excess rsETH is minted; raising `maxFeeMintAmountPerDay` or waiting for the 24-hour reset does not undo the already-minted excess.

## Likelihood Explanation
No special privileges are required. `updateRSETHPrice()` is callable by any address (`public whenNotPaused`). The daily limit is consumed by normal keeper/bot activity. `maxFeeMintAmountPerDay` has no on-chain floor relative to actual daily fee accrual; as protocol TVL grows, daily fee accrual grows proportionally, making a fixed cap increasingly likely to be exhausted within a single day. Once exhausted, the condition is self-sustaining for up to 24 hours. Deposits via `depositETH()` and `depositAsset()` continue to work normally during this window, so the dilution path is always open.

## Recommendation
Decouple the fee-minting cap from the price-update path. When the daily limit is exhausted, skip the fee mint but still write the new price:

```solidity
if (protocolFeeInETH > 0) {
    uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);
    if (currentPeriodMintedFeeAmount + rsethAmountToMintAsProtocolFee <= maxFeeMintAmountPerDay) {
        _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
        IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
        emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
    }
    // else: fee deferred; price still updated
} else {
    _checkAndUpdateDailyFeeMintLimit(0);
}
rsETHPrice = newRsETHPrice; // always written
```

Alternatively, accumulate deferred fees and mint them in the next period once capacity is available.

## Proof of Concept
1. Deploy with `maxFeeMintAmountPerDay = 100e18`.
2. Protocol TVL is large; each `updateRSETHPrice()` call computes `rsethAmountToMintAsProtocolFee ≈ 10e18`.
3. Call `updateRSETHPrice()` 10 times within the same 24-hour window → `currentPeriodMintedFeeAmount == 100e18`, price is updated each time.
4. Staking rewards continue to accrue; `totalETHInProtocol > previousTVL` on the next call.
5. Call `updateRSETHPrice()` again → `_checkAndUpdateDailyFeeMintLimit(10e18)` evaluates `100e18 + 10e18 > 100e18` → `revert DailyFeeMintLimitExceeded`. `rsETHPrice` is not written.
6. Call `updateRSETHPriceAsManager()` as the LRT manager → same internal path, same revert.
7. `rsETHPrice` remains stale (lower than true value) for up to 24 hours.
8. Any user calling `depositETH(0, "")` or `depositAsset(asset, amount, 0, "")` during this window receives `amount * assetPrice / staleRsETHPrice` rsETH, which is greater than `amount * assetPrice / trueRsETHPrice`. The excess rsETH permanently dilutes existing holders.

Foundry fork test outline:
- Fork mainnet, warp to mid-period.
- Call `updateRSETHPrice()` in a loop until `currentPeriodMintedFeeAmount == maxFeeMintAmountPerDay`.
- Assert next `updateRSETHPrice()` reverts with `DailyFeeMintLimitExceeded`.
- Record `rsETHPrice` before and after a simulated reward accrual; assert they are equal (price frozen).
- Deposit ETH; assert minted rsETH exceeds the amount that would be minted at the true price. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** contracts/LRTOracle.sol (L299-313)
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

        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L516-521)
```text
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
