Audit Report

## Title
Unguarded `_withdrawFromAave()` Call in `_processWithdrawalCompletion()` Temporarily Freezes All ETH Withdrawals When Aave Is Paused - (`contracts/LRTWithdrawalManager.sol`)

## Summary

`LRTWithdrawalManager::_processWithdrawalCompletion()` calls `_withdrawFromAave()` with no `try/catch` or pause-state guard when the contract's idle ETH balance is insufficient to cover a user's withdrawal. If Aave's pool is paused, `aaveWETHGateway.withdrawETH()` reverts, causing every `completeWithdrawal(ETH_TOKEN, ...)` call to revert for any user whose request requires pulling ETH from Aave. Because the protocol actively deposits unlocked ETH into Aave, this condition applies to nearly all pending ETH withdrawal requests, constituting a temporary but complete freeze of user funds.

## Finding Description

When `isAaveIntegrationEnabled == true` and the asset is ETH, `_processWithdrawalCompletion()` checks whether `address(this).balance < request.expectedAssetAmount` and, if so, calls `_withdrawFromAave(amountNeeded)` unconditionally:

```solidity
// LRTWithdrawalManager.sol L720-731
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);   // no try/catch
        ...
    }
}
```

`_withdrawFromAave()` calls `aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this))` at line 917 with no check on Aave's pause state. If Aave is paused, this external call reverts, propagating the revert up through `_processWithdrawalCompletion()` and out of `completeWithdrawal()`.

The deposit path in `unlockQueue()` (lines 311–316) correctly wraps the equivalent Aave call in a `try/catch`, emitting `AaveDepositFailed` and leaving funds in the contract on failure. No equivalent protection exists on the withdrawal path.

The `emergencyWithdrawFromAave()` function (line 560) also calls `_withdrawFromAave()` internally, so it too reverts when Aave is paused, eliminating the intended operator escape hatch. The only remaining mitigation is calling `setAaveIntegrationEnabled(false)`, which skips the Aave withdrawal block entirely — but users whose requests exceed the contract's idle ETH balance remain unable to complete withdrawals until Aave unpauses.

## Impact Explanation

**Medium — Temporary freezing of funds.** All ETH withdrawal completions that require pulling from Aave are blocked for the duration of an Aave pause. Users who have already had their withdrawal requests unlocked and passed the delay period cannot retrieve their ETH. The freeze lifts only when Aave unpauses or an operator successfully disables the integration and sufficient idle ETH exists in the contract to cover pending requests.

## Likelihood Explanation

Aave v3 has a documented global pause mechanism exercisable by Aave governance or its guardian. Aave has been paused in the past during security incidents. No attacker action is required; the trigger is an external governance action on Aave. Because `unlockQueue()` deposits unlocked ETH into Aave (lines 310–316), the contract's idle ETH balance is typically near zero, making the Aave withdrawal path the critical one for virtually every `completeWithdrawal` call. Any Aave pause while the integration is active immediately affects all pending ETH withdrawals.

## Recommendation

Wrap the `_withdrawFromAave()` call inside `_processWithdrawalCompletion()` in a `try/catch`, mirroring the deposit path. On failure, revert with a descriptive error (e.g., `AaveUnavailable`) so users understand the cause. Additionally, ensure `emergencyWithdrawFromAave()` is not the sole operator escape hatch — expose a separate function to disable the Aave integration and allow withdrawals to be served from idle contract balance, callable even when Aave is paused.

## Proof of Concept

1. Aave integration is enabled; `unlockQueue()` has deposited 100 ETH into Aave. `address(LRTWithdrawalManager).balance ≈ 0`.
2. Alice's 10 ETH withdrawal request is unlocked and past the delay period.
3. Aave governance pauses the Aave pool.
4. Alice calls `completeWithdrawal(ETH_TOKEN, "")`.
5. `_processWithdrawalCompletion()` finds `address(this).balance < 10 ETH`, computes `amountNeeded`, and calls `_withdrawFromAave(amountNeeded)`.
6. `_withdrawFromAave()` calls `aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this))` — this reverts because Aave is paused.
7. Alice's transaction reverts. Every other user's `completeWithdrawal` for ETH also reverts.
8. Operator attempts `emergencyWithdrawFromAave(type(uint256).max)` — this also calls `_withdrawFromAave()` and reverts for the same reason.
9. All ETH withdrawals remain frozen until Aave unpauses.

**Foundry fork test outline:**
```solidity
// Fork mainnet, enable Aave integration, deposit ETH to Aave via unlockQueue
// Impersonate Aave guardian, call pool.setPoolPause(true)
// vm.prank(alice); lrtWithdrawalManager.completeWithdrawal(ETH_TOKEN, "");
// vm.expectRevert(); // confirms freeze
```