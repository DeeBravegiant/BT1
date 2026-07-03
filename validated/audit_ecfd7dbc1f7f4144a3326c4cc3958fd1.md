Audit Report

## Title
`LRTDepositPool.getAssetCurrentLimit()` Ignores rsETH Daily Mint Cap, Returning Inflated Deposit Headroom - (File: contracts/LRTDepositPool.sol)

## Summary
`LRTDepositPool.getAssetCurrentLimit()` reports remaining deposit capacity based solely on the per-asset `depositLimitByAsset` cap stored in `LRTConfig`, but never consults the independent `maxMintAmountPerDay` daily mint cap enforced inside `RSETH.mint()`. When the daily rsETH quota is exhausted, the function returns a large positive value while every actual deposit call reverts with `DailyMintLimitExceeded`. No funds are lost, but the function fails to deliver its promised return value.

## Finding Description
`getAssetCurrentLimit()` ( [1](#0-0) ) computes only `depositLimitByAsset - totalAssetDeposits` and returns that value. It has no awareness of the rsETH daily mint cap.

The deposit execution path is: `depositAsset()` / `depositETH()` → `_beforeDeposit()` → `_checkIfDepositAmountExceedesCurrentLimit()` (checks only `depositLimitByAsset`) → `_mintRsETH()` → `RSETH.mint()`. [2](#0-1) 

`RSETH.mint()` applies the `checkDailyMintLimit` modifier: [3](#0-2) 

That modifier enforces a completely separate cap and reverts with `DailyMintLimitExceeded` if the daily quota is consumed: [4](#0-3) 

`RSETH` exposes `remainingDailyMintLimit()` which correctly accounts for period resets: [5](#0-4) 

`getAssetCurrentLimit()` never calls this function. The two caps are entirely independent, and only one is reflected in the public view.

## Impact Explanation
The function is the canonical on-chain view for deposit headroom. When the daily rsETH quota is exhausted, it returns a materially incorrect positive value while all deposit calls revert. No funds are permanently lost because the deposit transaction simply reverts. This maps exactly to the allowed Low impact: **Contract fails to deliver promised returns, but doesn't lose value.**

## Likelihood Explanation
`maxMintAmountPerDay` is an active operational control. Once the daily quota is consumed — during high-demand periods or after a single large institutional deposit — the discrepancy is live for the remainder of the 24-hour window. Any integrator (smart contract router, aggregator, keeper) polling `getAssetCurrentLimit()` during that window receives a wrong answer. The condition is routine and predictable, making likelihood **Medium**.

## Recommendation
`getAssetCurrentLimit()` should also cap its return value by the remaining rsETH daily mint headroom. Retrieve `RSETH.remainingDailyMintLimit()`, convert it to asset units via the oracle (the same oracle path used in `getRsETHAmountToMint()`), and return the minimum of the two headrooms:

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

## Proof of Concept
1. `depositLimitByAsset[stETH] = 10_000e18`, `getTotalAssetDeposits(stETH) = 1_000e18`. `getAssetCurrentLimit(stETH)` returns `9_000e18`.
2. Earlier in the same 24-hour window a large deposit consumed the full `maxMintAmountPerDay` quota. `RSETH.remainingDailyMintLimit()` returns `0`.
3. An integrator reads `getAssetCurrentLimit(stETH) == 9_000e18` and submits a deposit of `100e18 stETH`.
4. Execution reaches `RSETH.mint()`, the `checkDailyMintLimit` modifier fires, and the call reverts with `DailyMintLimitExceeded`. [6](#0-5) 
5. The integrator's transaction fails despite `getAssetCurrentLimit()` having indicated ample capacity.

**Foundry test plan:** Fork mainnet (or a local deployment), set `maxMintAmountPerDay` to a small value, perform a deposit that exhausts it, then assert that `getAssetCurrentLimit()` returns a non-zero value while a subsequent `depositAsset()` call reverts with `DailyMintLimitExceeded`.

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

**File:** contracts/RSETH.sol (L229-240)
```text
    function mint(
        address to,
        uint256 amount
    )
        external
        onlyRole(LRTConstants.MINTER_ROLE)
        whenNotPaused
        checkDailyMintLimit(amount)
    {
        _enforceNotBlocked(to);
        _mint(to, amount);
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
