Audit Report

## Title
Principal-Only Cap in `_withdrawFromAave` Permanently Blocks ETH Withdrawals When `totalETHDepositedToAave = 0` and Aave Interest Remains - (`contracts/LRTWithdrawalManager.sol`)

## Summary
`_withdrawFromAave` caps the withdrawable amount to `min(aaveBalance, totalETHDepositedToAave)`. When `totalETHDepositedToAave = 0` but `aaveAWETH.balanceOf > 0` (accrued interest), the function silently returns 0. `_processWithdrawalCompletion` then unconditionally reverts with `InsufficientLiquidityForWithdrawal`, permanently blocking all pending ETH withdrawal completions for users whose rsETH was already burned in `unlockQueue`.

## Finding Description
In `_withdrawFromAave` (L911–915):
```solidity
uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave
    ? aaveBalance
    : totalETHDepositedToAave;

withdrawnAmount = amount > withdrawablePrincipal ? withdrawablePrincipal : amount;
if (withdrawnAmount == 0) return 0;
```
When `totalETHDepositedToAave = 0`, `withdrawablePrincipal = 0`, `withdrawnAmount = 0`, and the function returns 0 regardless of the actual aWETH balance held.

In `_processWithdrawalCompletion` (L720–730):
```solidity
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);
        uint256 balanceAfter = address(this).balance;
        if (balanceAfter < request.expectedAssetAmount) {
            revert InsufficientLiquidityForWithdrawal();
        }
    }
}
```
Since `_withdrawFromAave` returned 0, `balanceAfter == contractBalance`, and the revert fires unconditionally.

The state `totalETHDepositedToAave = 0` with `aaveBalance > 0` is a natural outcome of normal operation: deposit 100 ETH → interest accrues to 105 → users complete withdrawals totaling 100 ETH principal → `totalETHDepositedToAave = 0`, `aaveBalance = 5`. No privileged action or attacker is required.

No existing recovery path redirects the residual interest to the contract:
- `emergencyWithdrawFromAave` (L551–563): calls `_collectInterestToTreasury()` (routes interest to treasury), then `_withdrawFromAave(amount)` which still returns 0.
- `setAaveIntegrationEnabled(false)` (L486–501): calls `_collectInterestToTreasury()` (interest → treasury), re-reads `aaveBalance = 0`, skips `_withdrawFromAave`. Sets flag to false, but the contract holds no ETH, so subsequent `completeWithdrawal` calls still fail (ETH transfer reverts).
- `_collectInterestToTreasury` (L945–958) explicitly sends interest to treasury, not to `address(this)`.

rsETH is burned in `unlockQueue` at L305 (`IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned)`) before `completeWithdrawal` is ever called. Once burned, the user holds no rsETH and has no alternative recovery path.

## Impact Explanation
**Critical: Permanent freezing of funds.** Users whose rsETH was burned in `unlockQueue` and whose withdrawal requests are unlocked after `totalETHDepositedToAave` reaches 0 cannot complete their withdrawals. Every retry of `completeWithdrawal` reverts identically. The user has lost rsETH and cannot recover ETH. The only non-protocol path is an admin manually sending ETH to the contract, which is not guaranteed by the protocol.

## Likelihood Explanation
The triggering state arises through normal protocol operation with no attacker involvement. Any user whose withdrawal is unlocked after all principal has been withdrawn from Aave (while interest remains) is permanently affected. The condition becomes more likely as the protocol matures and Aave interest accumulates over time. Likelihood is **Medium**.

## Recommendation
Remove the principal-only cap when `totalETHDepositedToAave = 0` and `aaveBalance > 0`. Specifically, in `_withdrawFromAave`, if `withdrawablePrincipal == 0` and `aaveBalance > 0`, allow withdrawing up to `aaveBalance` (treating residual balance as available for user obligations). Alternatively, track accrued interest separately and allow it to be used for withdrawal completion before routing to treasury. A minimal fix:
```solidity
uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave
    ? aaveBalance
    : totalETHDepositedToAave;

// If all principal has been withdrawn but interest remains, allow using it
if (withdrawablePrincipal == 0 && aaveBalance > 0) {
    withdrawablePrincipal = aaveBalance;
}
```

## Proof of Concept
```
// Achievable state through normal operation:
// totalETHDepositedToAave = 0
// aaveAWETH.balanceOf(withdrawalManager) = 5e18  (pure accrued interest)
// address(withdrawalManager).balance = 0
// isAaveIntegrationEnabled = true
// User has an unlocked withdrawal request for 1e18 ETH (rsETH already burned in unlockQueue)

// Call: withdrawalManager.completeWithdrawal(ETH_TOKEN, "")
// Execution path:
//   contractBalance = 0 < 1e18 = request.expectedAssetAmount
//   amountNeeded = 1e18
//   _withdrawFromAave(1e18):
//     aaveBalance = 5e18, totalETHDepositedToAave = 0
//     withdrawablePrincipal = min(5e18, 0) = 0
//     withdrawnAmount = min(1e18, 0) = 0
//     returns 0  [L915]
//   balanceAfter = 0 < 1e18
//   → revert InsufficientLiquidityForWithdrawal  [L729]
// All retry attempts revert identically. User funds permanently frozen.
```

Foundry fork test plan: fork mainnet with Aave v3, deploy `LRTWithdrawalManager`, deposit 100 ETH, simulate interest accrual by directly increasing aWETH balance, process user withdrawals until `totalETHDepositedToAave = 0`, then call `completeWithdrawal` and assert revert with `InsufficientLiquidityForWithdrawal`.