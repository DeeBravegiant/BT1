The code confirms all claims. Let me verify the mint function signature to ensure `checkDailyMintLimit` is applied there.

Audit Report

## Title
`LRTDepositPool.getAssetCurrentLimit()` Ignores rsETH Daily Mint Cap, Returning Inflated Deposit Headroom - (File: contracts/LRTDepositPool.sol)

## Summary
`LRTDepositPool.getAssetCurrentLimit()` only checks the per-asset `depositLimitByAsset` cap stored in `LRTConfig`, but never consults the independent `maxMintAmountPerDay` daily mint cap enforced by the `checkDailyMintLimit` modifier inside `RSETH.mint()`. When the daily rsETH quota is exhausted, the function returns a large positive headroom value while every actual deposit call will revert with `DailyMintLimitExceeded`. No funds are lost, but the function fails to deliver its promised return of accurate deposit capacity.

## Finding Description
`getAssetCurrentLimit()` is defined as:

```solidity
// contracts/LRTDepositPool.sol L402-409
function getAssetCurrentLimit(address asset) public view override returns (uint256) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
        return 0;
    }
    return lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;
}
```

The deposit execution path is: `depositAsset()` → `_beforeDeposit()` → `_checkIfDepositAmountExceedesCurrentLimit()` (checks only `depositLimitByAsset`) → `_mintRsETH()` → `RSETH.mint()`.

`RSETH.mint()` applies the `checkDailyMintLimit` modifier:

```solidity
// contracts/RSETH.sol L42-56
modifier checkDailyMintLimit(uint256 amount) {
    if (block.timestamp >= periodStartTime + 1 days) {
        currentPeriodMintedAmount = 0;
        periodStartTime = getCurrentPeriodStartTime();
    }
    if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
        revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
    }
    currentPeriodMintedAmount += amount;
    _;
}
```

`RSETH` exposes a dedicated view for the remaining daily quota:

```solidity
// contracts/RSETH.sol L265-272
function remainingDailyMintLimit() external view returns (uint256) {
    if (maxMintAmountPerDay == 0) return 0;
    uint256 effectiveDailyMintAmount = (block.timestamp >= periodStartTime + 1 days) ? 0 : currentPeriodMintedAmount;
    return maxMintAmountPerDay > effectiveDailyMintAmount ? maxMintAmountPerDay - effectiveDailyMintAmount : 0;
}
```

`getAssetCurrentLimit()` never calls `remainingDailyMintLimit()`. The two caps are entirely independent, and the view function only reflects one of them. [1](#0-0) [2](#0-1) [3](#0-2) 

## Impact Explanation
The function is the canonical on-chain view for deposit headroom. When the daily rsETH quota is exhausted, it returns a materially incorrect positive value. Any integrator — smart contract router, aggregator, or off-chain keeper — that relies on this return value will submit transactions that revert. No funds are permanently lost or frozen; the deposit simply fails. This maps exactly to the allowed Low impact: **Contract fails to deliver promised returns, but doesn't lose value.** [1](#0-0) 

## Likelihood Explanation
`maxMintAmountPerDay` is an active, configurable operational control. Once the daily quota is consumed — during high-demand periods or after a single large institutional deposit — the discrepancy is live for the remainder of the 24-hour window. Any external caller polling `getAssetCurrentLimit()` during that window receives a wrong answer. No special privileges are required; the condition is routine and predictable. [4](#0-3) 

## Recommendation
`getAssetCurrentLimit()` should also cap its return value by the remaining rsETH daily mint headroom. The `remainingDailyMintLimit()` view already exists on `RSETH`. The fix requires converting the rsETH headroom to asset units via the oracle and returning the minimum of the two headrooms:

```solidity
function getAssetCurrentLimit(address asset) public view override returns (uint256) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
        return 0;
    }
    uint256 assetHeadroom = lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;

    address rsethToken = lrtConfig.rsETH();
    uint256 rsethHeadroom = IRSETH(rsethToken).remainingDailyMintLimit();
    uint256 rsethHeadroomInAsset = convertRsETHToAsset(asset, rsethHeadroom);

    return assetHeadroom < rsethHeadroomInAsset ? assetHeadroom : rsethHeadroomInAsset;
}
```

Note: the recommendation in the submitted report references `getRemainingMintableAmount()`, but the actual function name in the deployed contract is `remainingDailyMintLimit()`. [3](#0-2) 

## Proof of Concept
1. `depositLimitByAsset[stETH] = 10_000e18`, `getTotalAssetDeposits(stETH) = 1_000e18`. `getAssetCurrentLimit(stETH)` returns `9_000e18`.
2. Earlier in the same 24-hour window, minting consumed the full `maxMintAmountPerDay`. `remainingDailyMintLimit()` returns `0`.
3. An integrator reads `getAssetCurrentLimit(stETH) == 9_000e18` and submits a deposit of `100e18 stETH`.
4. Execution reaches `RSETH.mint()`, the `checkDailyMintLimit` modifier fires, and the call reverts with `DailyMintLimitExceeded(currentPeriodMintedAmount + 100e18, maxMintAmountPerDay)`.
5. The integrator's transaction fails despite `getAssetCurrentLimit()` having indicated ample capacity.

A Foundry fork test can reproduce this by: (a) deploying or forking the contracts, (b) minting rsETH up to `maxMintAmountPerDay` via a direct privileged call, (c) asserting `getAssetCurrentLimit(stETH) > 0`, and (d) asserting that a subsequent `depositAsset(stETH, 1e18, 0)` reverts with `DailyMintLimitExceeded`. [5](#0-4) [4](#0-3)

### Citations

**File:** contracts/LRTDepositPool.sol (L402-409)
```text
    function getAssetCurrentLimit(address asset) public view override returns (uint256) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
            return 0;
        }

        return lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;
    }
```

**File:** contracts/LRTDepositPool.sol (L676-682)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```

**File:** contracts/RSETH.sol (L42-56)
```text
    modifier checkDailyMintLimit(uint256 amount) {
        // Check if we need to reset the period if it has been more than 24 hours
        if (block.timestamp >= periodStartTime + 1 days) {
            currentPeriodMintedAmount = 0;
            periodStartTime = getCurrentPeriodStartTime();
        }

        // Check if minting would exceed the daily limit
        if (currentPeriodMintedAmount + amount > maxMintAmountPerDay) {
            revert DailyMintLimitExceeded(currentPeriodMintedAmount + amount, maxMintAmountPerDay);
        }

        currentPeriodMintedAmount += amount;
        _;
    }
```

**File:** contracts/RSETH.sol (L265-272)
```text
    function remainingDailyMintLimit() external view returns (uint256) {
        if (maxMintAmountPerDay == 0) return 0;

        // If we're on a new day but no mint has occurred yet, treat currentPeriodMintedAmount as 0
        uint256 effectiveDailyMintAmount = (block.timestamp >= periodStartTime + 1 days) ? 0 : currentPeriodMintedAmount;

        return maxMintAmountPerDay > effectiveDailyMintAmount ? maxMintAmountPerDay - effectiveDailyMintAmount : 0;
    }
```
