Audit Report

## Title
Silent Zero Return in `_withdrawFromAave` When `totalETHDepositedToAave == 0` With Residual aWETH Balance Causes Temporary ETH Withdrawal Freeze — (`contracts/LRTWithdrawalManager.sol`)

## Summary
When `totalETHDepositedToAave == 0` but `aaveAWETH.balanceOf(address(this)) > 0` (an interest residual left after the final principal withdrawal), `_withdrawFromAave` silently returns 0 instead of reverting, causing `_processWithdrawalCompletion` to revert with `InsufficientLiquidityForWithdrawal` for all ETH withdrawal completions. This state is reachable in normal operation whenever interest accrues between the last `collectInterestToTreasury` call and the final principal withdrawal. The emergency recovery path (`emergencyWithdrawFromAave`) also fails in this state due to a sequencing bug.

## Finding Description
`_withdrawFromAave` at line 912 computes:
```solidity
uint256 withdrawablePrincipal = aaveBalance < totalETHDepositedToAave ? aaveBalance : totalETHDepositedToAave;
```
When `totalETHDepositedToAave == 0`, this always evaluates to `0` regardless of `aaveBalance`. Line 914–915 then returns 0 silently. The guard at line 909 (`if (aaveBalance == 0) revert`) does not fire because `aaveBalance > 0`.

In `_processWithdrawalCompletion` (lines 720–731), the return value of `_withdrawFromAave` is ignored; the code checks `address(this).balance` after the call. Since no ETH was actually withdrawn, `balanceAfter < request.expectedAssetAmount` and the function reverts with `InsufficientLiquidityForWithdrawal`. Because this revert unwinds the entire transaction, the `delete withdrawalRequests[requestId]` at line 712 is also reverted, so the user's request is not lost but cannot be completed.

The state `totalETHDepositedToAave == 0, aaveBalance > 0` is reached in normal operation:
1. 100 ETH deposited → `totalETHDepositedToAave = 100`, `aaveBalance ≈ 100`
2. Interest accrues → `aaveBalance = 100.5`
3. `_withdrawFromAave(100)` called for user withdrawals: `withdrawablePrincipal = min(100.5, 100) = 100`, withdraws 100, sets `totalETHDepositedToAave = 0`, but `aaveBalance = 0.5` remains

Simultaneously, all view/health functions mislead operators:
- `getAaveWithdrawableLiquidity()` returns the Aave pool's total WETH balance (unrelated to the contract's position) — always large
- `_checkAaveHealth()` returns `true` because `principal (0) > aaveBalance (0.5)` is false (line 931)
- `collectInterestToTreasury()` passes the health check and succeeds

`emergencyWithdrawFromAave` (lines 551–563) also fails: it checks `aaveBalance == 0` (passes, since 0.5 ETH residual exists), then calls `_collectInterestToTreasury()` which drains the residual to treasury, then calls `_withdrawFromAave(amount)` with `aaveBalance` now 0 → reverts with `InsufficientAaveBalance`.

## Impact Explanation
**Medium — Temporary freezing of funds.** All ETH withdrawal completions revert while `isAaveIntegrationEnabled == true` and the contract holds insufficient ETH to cover requests without Aave. User requests remain in the queue (not lost). Recovery requires an operator to call `collectInterestToTreasury()` (which succeeds since `_checkAaveHealth()` incorrectly returns true) and then `setAaveIntegrationEnabled(false)`, after which withdrawals proceed from the unstaking vault. The freeze persists until an operator diagnoses the state — delayed by the misleading view functions.

## Likelihood Explanation
Medium. No adversarial action is required. The state arises in normal protocol operation whenever Aave interest accrues between the last interest collection and the final principal withdrawal. Any deployment with active Aave integration and ongoing user withdrawals will eventually reach this state. It is not a one-time edge case but a recurring condition.

## Recommendation
1. In `_withdrawFromAave`, when `totalETHDepositedToAave == 0` and `aaveBalance > 0`, revert with a descriptive error (e.g., `OnlyInterestResidualRemaining`) rather than silently returning 0.
2. Fix `emergencyWithdrawFromAave` to re-read `aaveBalance` after `_collectInterestToTreasury()` and skip `_withdrawFromAave` if the balance is now 0.
3. Fix `getAaveWithdrawableLiquidity` to return `min(WETH_balance_of_aWETH_contract, totalETHDepositedToAave)` to reflect the contract's actual withdrawable principal.
4. Fix `_checkAaveHealth` to return `false` when `totalETHDepositedToAave == 0` and `aaveBalance > 0` (orphaned residual state).

## Proof of Concept
```solidity
// Prerequisites (reachable via normal operation):
// totalETHDepositedToAave = 0
// aaveAWETH.balanceOf(address(withdrawalManager)) = 0.5 ETH (interest residual)
// isAaveIntegrationEnabled = true
// address(withdrawalManager).balance = 0 ETH

// Step 1: View functions falsely show healthy state
assert(withdrawalManager.getAaveWithdrawableLiquidity() > 0); // pool WETH balance, unrelated
assert(withdrawalManager.getAaveBalance() > 0);               // 0.5 ETH residual
assert(withdrawalManager.aaveHealthCheck() == true);          // principal(0) <= balance(0.5)

// Step 2: User calls completeWithdrawal() for a 1 ETH request
// _processWithdrawalCompletion:
//   contractBalance = 0 < 1 ETH → enters Aave block
//   _withdrawFromAave(1 ETH):
//     aaveBalance = 0.5 > 0 → no revert at line 909
//     withdrawablePrincipal = min(0.5, 0) = 0
//     withdrawnAmount = 0 → returns 0 silently
//   balanceAfter = 0 < 1 ETH → revert InsufficientLiquidityForWithdrawal

// Step 3: Emergency recovery also fails
// emergencyWithdrawFromAave(0.5 ETH):
//   aaveBalance = 0.5 > 0 → passes line 555
//   _collectInterestToTreasury() → withdraws 0.5 ETH to treasury, aaveBalance = 0
//   _withdrawFromAave(0.5 ETH):
//     aaveBalance = 0 → revert InsufficientAaveBalance
```