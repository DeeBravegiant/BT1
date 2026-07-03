Audit Report

## Title
No-Escape Aave Pause Trap: All ETH Recovery Paths Revert When Aave WETH Reserve Is Paused — (`contracts/LRTWithdrawalManager.sol`)

## Summary
`LRTWithdrawalManager` deposits user ETH into Aave v3 and relies on `_withdrawFromAave` (which calls `aaveWETHGateway.withdrawETH` with no error handling) as the sole recovery mechanism. Every code path that can recover those funds — `setAaveIntegrationEnabled(false)`, `emergencyWithdrawFromAave`, and `completeWithdrawal` for ETH — unconditionally calls `_withdrawFromAave`. If Aave's WETH reserve is paused, all three paths revert, `isAaveIntegrationEnabled` remains stuck at `true`, and all user ETH deposited to Aave is inaccessible for the duration of the pause.

## Finding Description
`_withdrawFromAave` at line 917 calls `aaveWETHGateway.withdrawETH` with no try/catch:

```solidity
aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this));
```

**Path 1 — `setAaveIntegrationEnabled(false)` (L486–503):** The function calls `_withdrawFromAave(aaveBalance)` before assigning `isAaveIntegrationEnabled = enabled`. If Aave is paused, the assignment at L503 is never reached; the flag stays `true` and new ETH continues to be routed to Aave.

**Path 2 — `emergencyWithdrawFromAave` (L551–562):** Calls `_withdrawFromAave(amount)` unconditionally. `_collectInterestToTreasury` at L950 may silently return 0 (no interest), but `_withdrawFromAave` at L917 always calls `withdrawETH` when `withdrawnAmount > 0`, so the emergency path reverts identically.

**Path 3 — `completeWithdrawal` for ETH (L719–732):** When `address(this).balance < request.expectedAssetAmount`, it calls `_withdrawFromAave(amountNeeded)`. Because `unlockQueue` deposits all idle ETH to Aave via `depositToAaveExternal`, the contract's idle balance is typically 0, making this path always hit `_withdrawFromAave` and revert.

No alternative code path exists to clear `isAaveIntegrationEnabled` or serve user withdrawals without going through `_withdrawFromAave`.

## Impact Explanation
**Medium — Temporary freezing of funds.** All user ETH deposited to Aave is inaccessible for the duration of the Aave WETH reserve pause. `completeWithdrawal` reverts for every ETH withdrawal request, `emergencyWithdrawFromAave` reverts, and `setAaveIntegrationEnabled(false)` cannot clear the flag to stop further deposits. In the extreme case where Aave permanently deprecates the WETH reserve, the freeze becomes permanent (Critical), but the baseline realistic scenario is temporary freezing while the pause is active.

## Likelihood Explanation
Aave v3 governance can pause individual reserves via `PoolConfigurator.setReservePause`. This has occurred on mainnet (e.g., November 2022 CRV incident). The precondition — `isAaveIntegrationEnabled = true` and `totalETHDepositedToAave > 0` — is the normal operating state once the integration is active. No attacker action is required; the trigger is an Aave governance decision. Any user attempting `completeWithdrawal` during the pause will have their transaction revert.

## Recommendation
1. **Decouple flag-clearing from fund withdrawal.** Add a separate admin function that sets `isAaveIntegrationEnabled = false` without calling `_withdrawFromAave`, so new ETH stops being routed to Aave immediately regardless of Aave's state.
2. **Wrap `_withdrawFromAave` in try/catch in `emergencyWithdrawFromAave`.** Allow the emergency path to at least clear the flag and stop further deposits even if the withdrawal itself fails.
3. **In `completeWithdrawal`, handle Aave revert gracefully.** If `_withdrawFromAave` reverts, revert with a clear, non-blocking error rather than propagating the Aave revert, so other (non-ETH or idle-balance) withdrawals are not affected.
4. **Remove mandatory `_collectInterestToTreasury` from the emergency path.** Interest collection should not be a prerequisite for emergency fund recovery.

## Proof of Concept
```solidity
// 1. Deploy LRTWithdrawalManager with Aave integration enabled.
// 2. Call unlockQueue() to deposit user ETH to Aave (totalETHDepositedToAave > 0).
// 3. Aave governance pauses the WETH reserve (MockAaveGateway.setPaused(true)).

// 4. Admin attempts to disable Aave integration:
vm.prank(manager);
vm.expectRevert(); // _withdrawFromAave → withdrawETH reverts
wm.setAaveIntegrationEnabled(false);
assertTrue(wm.isAaveIntegrationEnabled()); // flag unchanged

// 5. Pauser attempts emergency withdrawal:
vm.prank(pauser);
vm.expectRevert(); // same revert path
wm.emergencyWithdrawFromAave(type(uint256).max);

// 6. User attempts completeWithdrawal (idle balance = 0, all ETH in Aave):
vm.prank(user);
vm.expectRevert(); // _withdrawFromAave → withdrawETH reverts
wm.completeWithdrawal(LRTConstants.ETH_TOKEN, "");

// All ETH inaccessible while Aave reserve is paused.
```