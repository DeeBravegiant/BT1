Audit Report

## Title
`getAssetCurrentLimit()` Ignores RSETH Daily Mint Cap, Causing Deposit Revert — (`contracts/LRTDepositPool.sol`)

## Summary
`LRTDepositPool::getAssetCurrentLimit()` returns remaining deposit capacity based solely on the per-asset deposit cap in `lrtConfig`. It never consults the separate `maxMintAmountPerDay` limit enforced by the `checkDailyMintLimit` modifier in `RSETH::mint()`. When the daily rsETH mint cap is nearly exhausted, a user who queries `getAssetCurrentLimit()` and deposits the returned amount will receive a `DailyMintLimitExceeded` revert inside `RSETH::mint()`, wasting gas and making the deposit impossible until the next daily period resets.

## Finding Description
`getAssetCurrentLimit()` at `contracts/LRTDepositPool.sol` L402–409 computes only the per-asset cap remainder:

```solidity
function getAssetCurrentLimit(address asset) public view override returns (uint256) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
        return 0;
    }
    return lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;
}
```

`_beforeDeposit()` at L648–670 likewise only validates against the per-asset cap via `_checkIfDepositAmountExceedesCurrentLimit()` at L676–682, which checks `totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)`. Neither function consults `RSETH`.

After `_beforeDeposit` passes, `_mintRsETH()` at L686–690 calls `IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint)`. The `mint()` function in `contracts/RSETH.sol` at L229–240 applies the `checkDailyMintLimit(amount)` modifier (L42–56), which reverts with `DailyMintLimitExceeded` if `currentPeriodMintedAmount + amount > maxMintAmountPerDay`. This check is entirely independent of the per-asset deposit cap and is never surfaced by `getAssetCurrentLimit()`.

`RSETH` exposes `remainingDailyMintLimit()` at L265–272 for exactly this purpose, but `LRTDepositPool` never calls it.

## Impact Explanation
This matches the allowed impact **"Low: Contract fails to deliver promised returns, but doesn't lose value."** `getAssetCurrentLimit()` is a public view function that users and integrators rely on to determine the maximum safe deposit. When the daily rsETH mint cap is active and nearly exhausted, the function returns an inflated value. Any deposit submitted for that amount reverts atomically (no funds are lost since Solidity reverts the entire transaction including the `safeTransferFrom`), but the user wastes gas and cannot deposit until the next 24-hour period resets.

## Likelihood Explanation
Requires `maxMintAmountPerDay` to be set to a non-zero value via `setMaxMintAmountPerDay()` and `currentPeriodMintedAmount` to be close to that cap while the per-asset deposit limit still has remaining capacity. Both limits are independently configurable by the LRT manager. During high-volume deposit periods, the daily rsETH cap can be approached while per-asset limits remain open, making this a realistic operational state. Any unprivileged depositor can trigger the revert simply by calling `depositAsset()` or `depositETH()` with the value returned by `getAssetCurrentLimit()`.

## Recommendation
`getAssetCurrentLimit()` should also account for the remaining rsETH daily mint capacity. Convert the remaining rsETH mint headroom to asset units using the oracle and return the minimum of the two limits:

```solidity
function getAssetCurrentLimit(address asset) public view override returns (uint256) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
        return 0;
    }
    uint256 assetLimit = lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;

    uint256 rsethDailyRemaining = IRSETH(lrtConfig.rsETH()).remainingDailyMintLimit();
    if (rsethDailyRemaining == 0) return 0;

    // Convert rsETH remaining to asset units: rsethRemaining * rsETHPrice / assetPrice
    address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
    ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);
    uint256 assetEquivalent = rsethDailyRemaining * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);

    return assetLimit < assetEquivalent ? assetLimit : assetEquivalent;
}
```

`_beforeDeposit()` should similarly validate the rsETH daily mint headroom before proceeding.

## Proof of Concept
1. LRT manager calls `RSETH.setMaxMintAmountPerDay(1000e18)`. `currentPeriodMintedAmount` is `990e18` (10 rsETH remaining in the current period).
2. The per-asset deposit limit for stETH has `500e18` stETH of remaining capacity.
3. User calls `LRTDepositPool.getAssetCurrentLimit(stETH)` → returns `500e18` (daily rsETH cap ignored).
4. User calls `depositAsset(stETH, 500e18, 0, "")`.
5. `_beforeDeposit` passes: `totalAssetDeposits + 500e18 ≤ depositLimitByAsset(stETH)`.
6. `getRsETHAmountToMint(stETH, 500e18)` returns ~`500e18` rsETH (assuming 1:1 price).
7. `safeTransferFrom` moves 500 stETH from user to pool.
8. `_mintRsETH(500e18)` calls `RSETH.mint(user, 500e18)`.
9. `checkDailyMintLimit`: `990e18 + 500e18 = 1490e18 > 1000e18` → reverts `DailyMintLimitExceeded`.
10. Entire transaction reverts. User loses gas; deposit is impossible until the next 24-hour period.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/LRTDepositPool.sol (L686-690)
```text
    function _mintRsETH(uint256 rsethAmountToMint) private {
        address rsethToken = lrtConfig.rsETH();
        // mint rseth for user
        IRSETH(rsethToken).mint(msg.sender, rsethAmountToMint);
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
