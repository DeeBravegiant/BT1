Audit Report

## Title
Withdrawal Payout Hard-Capped at Initiation-Time Value Burns Full rsETH While Underpaying Accrued Yield - (`contracts/LRTWithdrawalManager.sol`)

## Summary
`_calculatePayoutAmount()` returns `min(expectedAssetAmount, currentReturn)`, so whenever rsETH appreciates during the withdrawal delay the user receives the stale initiation-time value while their full rsETH is burned. The yield that accrued during the delay is not returned to the user; it remains in the unstaking vault and is redistributed to subsequent withdrawers, constituting a direct, permanent loss of unclaimed yield for every ordinary withdrawer.

## Finding Description
At initiation, `initiateWithdrawal()` records `expectedAssetAmount` using the rsETH price at that block and transfers the user's rsETH to the contract:

```solidity
// LRTWithdrawalManager.sol L168-175
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
assetsCommitted[asset] += expectedAssetAmount;
_addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
```

At unlock time, `_calculatePayoutAmount()` computes the current fair value but discards it in favour of the lower initiation-time figure:

```solidity
// LRTWithdrawalManager.sol L833-834
uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```

`_unlockWithdrawalRequests()` then burns the **full** `rsETHUnstaked` while only unlocking `payoutAmount` (the minimum):

```solidity
// LRTWithdrawalManager.sol L802-807
assetsCommitted[asset] -= request.expectedAssetAmount;
request.expectedAssetAmount = payoutAmount;   // overwritten with minimum
rsETHAmountToBurn += request.rsETHUnstaked;   // full rsETH burned
availableAssetAmount -= payoutAmount;
assetAmountToUnlock += payoutAmount;
```

The full rsETH is subsequently burned and only `assetAmountUnlocked` (the capped sum) is redeemed from the vault:

```solidity
// LRTWithdrawalManager.sol L305-307
if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
unstakingVault.redeem(asset, assetAmountUnlocked);
```

The vault's actual balance (`unstakingVault.balanceOf(asset)`, used as `totalAvailableAssets` in `_createUnlockParams`) grows over time as rebasing LSTs (e.g., stETH) accrue staking rewards. The extra balance is present and available, but the `min` cap prevents it from reaching the user. No existing guard compensates the user for the discarded `currentReturn - expectedAssetAmount` difference.

## Impact Explanation
**High — Theft of unclaimed yield.** The user's rsETH, held by the withdrawal manager during the delay, appreciates in line with EigenLayer staking rewards. The user is entitled to the current fair value of that rsETH at redemption time. Instead, the full rsETH is destroyed and only the stale initiation-time value is paid out. The accrued yield is not returned; it stays in the vault and benefits future withdrawers. This is a direct, concrete transfer of the withdrawing user's unclaimed yield to the protocol/future users, matching the "High. Theft of unclaimed yield" impact class exactly.

## Likelihood Explanation
No special conditions, attacker capital, or privileged access are required. Any user who calls `initiateWithdrawal()` followed by `completeWithdrawal()` after the default 8-day delay (`withdrawalDelayBlocks = 8 days / 12 seconds`, L94) is affected whenever `updateRSETHPrice()` has been called in the interim — which operators are incentivised to do regularly. rsETH price appreciation is the expected, routine outcome of the protocol operating correctly. The loss is automatic and repeatable for every ordinary withdrawer.

## Recommendation
Replace the `min` logic with the current fair value:

```solidity
function _calculatePayoutAmount(
    WithdrawalRequest storage request,
    uint256 rsETHPrice,
    uint256 assetPrice
) private view returns (uint256) {
    return (request.rsETHUnstaked * rsETHPrice) / assetPrice;
}
```

If a slippage cap is desired for oracle-manipulation protection, apply it symmetrically (e.g., cap at a small percentage above `expectedAssetAmount`) rather than hard-capping at the initiation-time value. The `assetsCommitted` accounting should also be updated to reflect the actual payout rather than the stale committed amount.

## Proof of Concept
1. rsETH price at `t=0`: 1.05 ETH/rsETH; stETH price: 1.00 ETH/stETH. User calls `initiateWithdrawal(stETH, 100e18)`. `expectedAssetAmount = 100 * 1.05 / 1.00 = 105 stETH`. 100 rsETH transferred to contract.
2. 8 days pass. `updateRSETHPrice()` is called; rsETH price is now 1.06 ETH/rsETH (normal yield accrual). stETH price unchanged at 1.00.
3. Operator calls `unlockQueue`. `_calculatePayoutAmount` computes `currentReturn = 100 * 1.06 / 1.00 = 106 stETH`. Since `105 < 106`, returns `105`.
4. `rsETHAmountToBurn += 100e18` (full burn); `assetAmountToUnlock += 105e18` (capped). The vault holds ≥106 stETH (rebasing), but only 105 is redeemed.
5. User receives 105 stETH; 1 stETH of accrued yield remains in the vault. 100 rsETH is permanently destroyed.
6. Foundry fork test: deploy against mainnet state, fast-forward 8 days, call `updateRSETHPrice()`, call `unlockQueue`, assert `getUserWithdrawalRequest.expectedAssetAmount < currentFairValue` and that the vault retains the difference.