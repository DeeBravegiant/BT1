Audit Report

## Title
Withdrawal Payout Capped at Request-Time Price, Stranding Accrued Yield in Vault - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager._calculatePayoutAmount` returns `min(expectedAssetAmount, currentReturn)`, capping the user's payout at the stale request-time price even when rsETH has appreciated. The full `rsETHUnstaked` is burned at current value, but the user receives only the lower, locked-in amount. The difference in assets remains in `LRTUnstakingVault`, silently redistributed to remaining rsETH holders rather than returned to the withdrawing user.

## Finding Description
The withdrawal lifecycle creates an accounting gap across three confirmed code paths:

**Step 1 — `initiateWithdrawal` (L166–175):** `expectedAssetAmount` is computed at the current rsETH/asset price and stored in the `WithdrawalRequest`. rsETH is transferred to the contract and `assetsCommitted[asset]` is incremented by this stale amount. [1](#0-0) 

**Step 2 — `_calculatePayoutAmount` (L833–834):** At unlock time, `currentReturn = (rsETHUnstaked * rsETHPrice) / assetPrice` reflects the current fair value. The function returns `min(expectedAssetAmount, currentReturn)`. When rsETH has appreciated, `currentReturn > expectedAssetAmount`, so the user is capped at the lower stale value. [2](#0-1) 

**Step 3 — `_unlockWithdrawalRequests` (L802–807):** The full `request.rsETHUnstaked` is added to `rsETHAmountToBurn`, but only `payoutAmount` (the capped amount) is added to `assetAmountToUnlock`. `request.expectedAssetAmount` is overwritten with the capped `payoutAmount`. [3](#0-2) 

**Step 4 — `unlockQueue` (L305, L307):** The full `rsETHBurned` is burned via `burnFrom`, and only `assetAmountUnlocked` (the capped sum) is redeemed from the vault. [4](#0-3) 

**Step 5 — `_processWithdrawalCompletion` (L734):** The user receives `request.expectedAssetAmount`, which was overwritten to the capped `payoutAmount`. [5](#0-4) 

The gap: rsETH burned represents `currentReturn` worth of assets at unlock time. The user receives only `expectedAssetAmount`. The delta `currentReturn - expectedAssetAmount` is never redeemed from the vault and is never attributed to the user — it remains as excess backing for remaining rsETH holders.

## Impact Explanation
**High — Theft of unclaimed yield.** Every withdrawing user who experiences rsETH price appreciation between `initiateWithdrawal` and `unlockQueue` loses the yield accrued on their position during the delay window. Their rsETH is burned at full current value, but they receive only the stale, lower amount. The accrued yield is permanently transferred to remaining rsETH holders. This matches the allowed impact class "High. Theft of unclaimed yield" exactly: the yield belongs to the withdrawing user (it accrued on their rsETH), but it is not credited to them and is instead redistributed.

## Likelihood Explanation
**High.** rsETH price increases continuously as EigenLayer staking rewards accrue and are reflected via `LRTOracle.updateRSETHPrice`. The default withdrawal delay is 8 days (`withdrawalDelayBlocks = 8 days / 12 seconds` at L94). [6](#0-5) 
Over an 8-day window under normal protocol operation, rsETH price appreciation is near-certain. No attacker action is required — the loss is triggered automatically whenever an operator calls `unlockQueue` during a period of rsETH appreciation, which is the standard operating condition. Every queued withdrawal processed during normal protocol operation triggers this loss.

## Recommendation
In `_calculatePayoutAmount`, remove the upside cap so users receive the full current fair value of their rsETH at unlock time. The cap should only apply on the downside (slashing/price drop) to protect the vault from over-commitment:

```solidity
function _calculatePayoutAmount(
    WithdrawalRequest storage request,
    uint256 rsETHPrice,
    uint256 assetPrice
) private view returns (uint256) {
    uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
-   return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
+   return currentReturn; // user receives full current fair value; vault is protected on downside naturally
}
```

If the protocol intentionally caps upside (e.g., to prevent oracle-manipulation profit), the excess assets must be explicitly credited to the user or returned to the vault's accounting — not silently left as a windfall for remaining rsETH holders.

## Proof of Concept
1. rsETH price = 1.05 ETH/rsETH, stETH price = 1.00 ETH/stETH.
2. User calls `initiateWithdrawal(stETH, 1e18)`. `expectedAssetAmount = 1.05e18`. `assetsCommitted[stETH] += 1.05e18`.
3. 8 days pass. rsETH price rises to 1.08 ETH/rsETH (normal staking reward accrual).
4. Operator calls `unlockQueue(stETH, ...)`. `_calculatePayoutAmount` computes `currentReturn = 1.08e18`, returns `min(1.05e18, 1.08e18) = 1.05e18`.
5. `rsETHAmountToBurn += 1e18` (full rsETH burned). `assetAmountToUnlock += 1.05e18` (only 1.05e18 stETH redeemed from vault).
6. `burnFrom(address(this), 1e18)` executes — full rsETH destroyed.
7. User calls `completeWithdrawal`. Receives 1.05e18 stETH.
8. **Result**: User burned 1 rsETH worth 1.08 ETH at unlock time but received only 1.05 ETH. 0.03 ETH worth of stETH remains in the vault, permanently unattributed to the user who earned it.

**Foundry test plan**: Deploy with a mock oracle, call `initiateWithdrawal`, advance blocks past `withdrawalDelayBlocks`, update oracle price upward, call `unlockQueue`, call `completeWithdrawal`, assert `balanceOf(user) == 1.05e18` and vault balance retains the 0.03e18 delta — confirming the yield is stranded.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```

**File:** contracts/LRTWithdrawalManager.sol (L166-175)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L305-307)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L734-734)
```text
        _transferAsset(asset, user, request.expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L802-807)
```text
            assetsCommitted[asset] -= request.expectedAssetAmount;
            // Set the amount the user will receive
            request.expectedAssetAmount = payoutAmount;
            rsETHAmountToBurn += request.rsETHUnstaked;
            availableAssetAmount -= payoutAmount;
            assetAmountToUnlock += payoutAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```
