Audit Report

## Title
Block Stuffing Accumulates Multi-Day Yield Into Single Fee Mint, Triggering `DailyFeeMintLimitExceeded` and Freezing Price Updates — (`contracts/LRTOracle.sol`)

## Summary

When `updateRSETHPrice()` is suppressed for multiple days via block stuffing, the entire accumulated yield is presented as a single-period TVL increase. The resulting protocol fee mint amount can exceed `maxFeeMintAmountPerDay`, causing `_checkAndUpdateDailyFeeMintLimit` to revert with `DailyFeeMintLimitExceeded`. Both the public `updateRSETHPrice()` and the manager-only `updateRSETHPriceAsManager()` share the same internal path and are simultaneously blocked, requiring out-of-band manager intervention to recover.

## Finding Description

`updateRSETHPrice()` is a permissionless public function that delegates to `_updateRsETHPrice()`. [1](#0-0) 

`updateRSETHPriceAsManager()` provides no alternative internal path — it calls the identical `_updateRsETHPrice()`: [2](#0-1) 

Inside `_updateRsETHPrice()`, the TVL delta since the last update is treated as the reward for the current call, with no amortization over elapsed days: [3](#0-2) 

The computed fee is then passed to `_checkAndUpdateDailyFeeMintLimit`: [4](#0-3) 

`_checkAndUpdateDailyFeeMintLimit` resets the period counter when a new day begins, but still checks the full single-call fee amount against the per-day cap. If N days of yield are presented at once, the fee is N× the expected daily fee and the revert is unconditional: [5](#0-4) 

Asset prices are read live from rebasing oracles such as `SwETHPriceOracle`, which returns the current accumulated rate regardless of how many days have passed: [6](#0-5) 

The `DailyFeeMintLimitExceeded` error is defined in the interface and is the only outcome when the cap is breached: [7](#0-6) 

Recovery requires the manager to first call `setMaxFeeMintAmountPerDay` to raise the cap, then retry — a two-step manual intervention during which `rsETHPrice` remains stale. [8](#0-7) 

## Impact Explanation

`rsETHPrice` is not updated while the revert persists; all downstream consumers (deposit pool pricing, withdrawal valuations, pool exchange rates) read a stale price. Both the public and manager-gated entry points are simultaneously bricked. This matches the allowed impact **Low — Block stuffing**, which is explicitly in scope.

## Likelihood Explanation

Block stuffing on Ethereum mainnet is expensive but economically rational if the attacker profits from stale pricing (e.g., holds a large short position on rsETH or exploits a downstream protocol reading `rsETHPrice`). The attack requires no privileged access and is fully permissionless. The vulnerability is triggered by a small `maxFeeMintAmountPerDay` relative to the protocol's TVL and yield rate — a realistic configuration for a conservative daily cap. The attack is repeatable as long as the manager has not raised the cap.

## Recommendation

In `_checkAndUpdateDailyFeeMintLimit`, instead of reverting when the fee exceeds the daily cap, cap the minted fee at the remaining daily allowance and carry forward the uncollected fee, or skip fee minting for the excess. Alternatively, give `updateRSETHPriceAsManager()` its own internal path that bypasses the daily fee limit check (minting the full fee regardless of the cap), so the manager can always recover from a stuffed-block scenario without needing a separate `setMaxFeeMintAmountPerDay` call.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Foundry unit test — no mainnet required
// 1. Deploy LRTOracle with a mock swETH oracle returning rate = 1e18
// 2. Set maxFeeMintAmountPerDay = 1e15 (tiny cap)
// 3. Simulate N=7 days passing: advance block.timestamp by 7 days
//    WITHOUT calling updateRSETHPrice (simulating block stuffing)
// 4. Advance mock swETH rate to 1e18 + 7 * dailyYield (7 days of accrued yield)
// 5. Call updateRSETHPrice()
// 6. Assert revert with DailyFeeMintLimitExceeded

function testBlockStuffingDailyFeeLimitDOS() public {
    // setup: small daily fee cap
    lrtOracle.setMaxFeeMintAmountPerDay(1e15);

    // simulate 7 days of block stuffing — no updateRSETHPrice called
    vm.warp(block.timestamp + 7 days);

    // swETH rate has accrued 7 days of ~4% APR yield
    mockSwETH.setRate(1e18 + 7 * 1.1e13); // ~7 days of daily yield

    // anyone calls updateRSETHPrice — reverts
    vm.expectRevert(ILRTOracle.DailyFeeMintLimitExceeded.selector);
    lrtOracle.updateRSETHPrice();

    // even manager is blocked — same internal path, no bypass
    vm.prank(manager);
    vm.expectRevert(ILRTOracle.DailyFeeMintLimitExceeded.selector);
    lrtOracle.updateRSETHPriceAsManager();
}
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

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

**File:** contracts/oracles/SwETHPriceOracle.sol (L34-40)
```text
    function getAssetPrice(address asset) external view returns (uint256) {
        if (asset != swETHAddress) {
            revert InvalidAsset();
        }

        return ISwETH(swETHAddress).getRate();
    }
```

**File:** contracts/interfaces/ILRTOracle.sol (L10-10)
```text
    error DailyFeeMintLimitExceeded(uint256 currentAmount, uint256 maxAmount);
```
