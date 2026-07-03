Audit Report

## Title
Withdrawal Payout Hard-Capped at Initiation-Time Value Strips Yield Accrued During Delay - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`_calculatePayoutAmount()` returns `min(expectedAssetAmount, currentReturn)`, meaning when rsETH appreciates during the withdrawal delay (the normal case), the user receives the stale initiation-time value rather than the current fair value of their rsETH. The full `rsETHUnstaked` is burned regardless, so the yield that accrued during the delay is permanently lost to the withdrawing user and redistributed to the vault for future withdrawers.

## Finding Description
At initiation, `initiateWithdrawal()` records `expectedAssetAmount` using the rsETH price at that moment and reserves it via `assetsCommitted[asset] += expectedAssetAmount` (L168–173). At unlock time, `_calculatePayoutAmount()` (L833–834) computes `currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice` but returns `expectedAssetAmount` whenever `expectedAssetAmount < currentReturn` — i.e., whenever the rsETH price has risen. `_unlockWithdrawalRequests()` then sets `request.expectedAssetAmount = payoutAmount` (the minimum, L804), accumulates the full `rsETHUnstaked` into `rsETHAmountToBurn` (L805), and only unlocks `payoutAmount` from the vault (L807). The full rsETH is subsequently burned at L305 while only `assetAmountUnlocked` (the capped amount) is redeemed from the vault (L307). The delta between `currentReturn` and `payoutAmount` remains in the unstaking vault, available to future withdrawers. No existing guard compensates the user for this difference; the `min` logic is the sole determinant of payout.

## Impact Explanation
Every withdrawal where rsETH price rose during the delay — the routine, expected outcome of the protocol operating correctly — results in the user receiving less underlying asset than their rsETH is currently worth, while their full rsETH is burned. This is a direct, permanent loss of yield that belongs to the withdrawing user, matching the allowed impact: **High — Theft of unclaimed yield**.

## Likelihood Explanation
No special conditions, attacker capital, or privileged access are required. Any unprivileged user who calls `initiateWithdrawal()` followed by `completeWithdrawal()` after the 8-day delay (L94) is affected whenever `updateRSETHPrice()` has been called in the interim, which operators are incentivised to do regularly. This is the normal operating path for every withdrawer.

## Recommendation
Remove the `min` cap and always pay the current fair value of the rsETH at unlock time:

```solidity
function _calculatePayoutAmount(
    WithdrawalRequest storage request,
    uint256 rsETHPrice,
    uint256 assetPrice
) private view returns (uint256) {
    return (request.rsETHUnstaked * rsETHPrice) / assetPrice;
}
```

If slippage protection is desired, apply a symmetric cap (e.g., allow up to a small percentage above `expectedAssetAmount`) and ensure `assetsCommitted` accounting is updated accordingly to reserve the correct amount at initiation.

## Proof of Concept
1. rsETH price at `t=0`: 1.05 ETH/rsETH. User calls `initiateWithdrawal(stETH, 100e18)`. `expectedAssetAmount = 105e18` stETH. `assetsCommitted[stETH] += 105e18`.
2. 8 days pass. `updateRSETHPrice()` is called; rsETH price is now 1.06 ETH/rsETH (normal staking yield).
3. Operator calls `unlockQueue`. `_calculatePayoutAmount` computes `currentReturn = 100e18 * 1.06e18 / 1e18 = 106e18`. Since `105e18 < 106e18`, returns `105e18`.
4. `rsETHAmountToBurn += 100e18` (full rsETH), `assetAmountToUnlock += 105e18` (capped). 100 rsETH burned; user receives 105 stETH instead of 106 stETH.
5. The 1 stETH of yield accrued during the delay remains in the vault for future withdrawers.

**Foundry fork test plan**: Deploy against a mainnet fork, mint rsETH, call `initiateWithdrawal`, advance blocks by `withdrawalDelayBlocks`, mock `rsETHPrice` to a higher value via `LRTOracle`, call `unlockQueue`, assert `withdrawalRequests[requestId].expectedAssetAmount == 105e18` while `(100e18 * newPrice / assetPrice) == 106e18`, confirming the 1 stETH shortfall.