Audit Report

## Title
Aave WETH 100% Utilization Temporarily Freezes All ETH `completeWithdrawal` Calls With No Working Escape Hatch — (`contracts/LRTWithdrawalManager.sol`)

## Summary

When `isAaveIntegrationEnabled` is `true` and the Aave WETH pool reaches 100% utilization, `aaveWETHGateway.withdrawETH` reverts. Because `_withdrawFromAave` contains no error handling around this call (line 917), the revert propagates uncaught through `completeWithdrawal` (line 724), blocking every pending unlocked ETH withdrawal. Every administrative recovery path — `emergencyWithdrawFromAave`, `setAaveIntegrationEnabled(false)`, and `configureAaveIntegration` — also calls `_withdrawFromAave` without a try/catch, leaving no on-chain mechanism to unblock withdrawals until Aave liquidity naturally recovers.

## Finding Description

**Withdrawal path — bare call, no error handling:**

In `_processWithdrawalCompletion` (lines 720–731 of `contracts/LRTWithdrawalManager.sol`), when the contract's ETH balance is insufficient, `_withdrawFromAave(amountNeeded)` is called directly. Inside `_withdrawFromAave` (line 917), the call to `aaveWETHGateway.withdrawETH` is bare — any revert from Aave propagates directly to the caller:

```solidity
// line 917
aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this));
```

**Asymmetry with the deposit path:**

The deposit path (lines 311–316) correctly wraps the Aave call in a try/catch, silently failing and emitting `AaveDepositFailed`. The withdrawal path has no equivalent protection, despite the same failure mode being possible.

**All escape hatches fail under the same condition:**

- `emergencyWithdrawFromAave` (PAUSER_ROLE, line 560): calls `_withdrawFromAave(amount)` — reverts.
- `setAaveIntegrationEnabled(false)` (LRT Manager, lines 494–496): calls `_withdrawFromAave(aaveBalance)` — reverts, so `isAaveIntegrationEnabled` is never set to `false`.
- `configureAaveIntegration` (LRT Manager, lines 446–448): calls `_withdrawFromAave(aaveBalance)` — reverts.

None of these functions wrap `_withdrawFromAave` in a try/catch or provide a force-skip path. The manager cannot set `isAaveIntegrationEnabled = false` without first successfully draining Aave, which is impossible when utilization is 100%.

## Impact Explanation

**Medium — Temporary freezing of funds.**

All users with pending unlocked ETH withdrawal requests are unable to call `completeWithdrawal` for as long as Aave WETH utilization remains at or near 100%. The aWETH balance is intact and funds are not lost, but they are inaccessible for an indeterminate period entirely outside the protocol's control. No administrative action can unblock withdrawals while the condition persists, as every recovery path also reverts.

## Likelihood Explanation

Aave WETH utilization reaching 100% is a realistic, historically observed market condition during periods of high ETH demand or market stress. The preconditions — `isAaveIntegrationEnabled = true`, contract ETH balance below the withdrawal amount, and Aave at max utilization — can all occur simultaneously in normal operation, since the protocol actively deposits unlocked ETH into Aave via `unlockQueue` (lines 310–316). No attacker action is required; the condition arises from ordinary market dynamics.

## Recommendation

1. **Wrap `_withdrawFromAave` in `completeWithdrawal` with a try/catch.** If the Aave withdrawal fails, fall through to the existing `InsufficientLiquidityForWithdrawal` revert (line 729) rather than propagating the opaque Aave revert. This preserves existing error semantics.

2. **Decouple `setAaveIntegrationEnabled(false)` from `_withdrawFromAave`.** Allow the manager to set `isAaveIntegrationEnabled = false` even when Aave cannot be drained — skip the withdrawal if Aave reverts, leaving aWETH in place to be claimed later via a separate function.

3. **Add a `forceDisableAaveIntegration` function** (callable by PAUSER_ROLE or LRT Manager) that sets `isAaveIntegrationEnabled = false` without attempting to withdraw, for use when Aave is illiquid.

## Proof of Concept

```solidity
// Fork mainnet with Aave WETH at 100% utilization
// 1. Enable Aave integration; deposit ETH to Aave via unlockQueue
// 2. Simulate Aave WETH pool at 100% utilization (all WETH borrowed)
// 3. Call completeWithdrawal for an unlocked ETH request
//    where address(this).balance < request.expectedAssetAmount
//    → reverts with Aave's "not enough liquidity" error (not InsufficientLiquidityForWithdrawal)
// 4. Call emergencyWithdrawFromAave(type(uint256).max) → also reverts
// 5. Call setAaveIntegrationEnabled(false) → also reverts
// Result: isAaveIntegrationEnabled remains true, all ETH withdrawals blocked

function testAaveLiquidityExhaustionBlocksWithdrawals() public {
    vm.mockCallRevert(
        address(aaveWETHGateway),
        abi.encodeWithSelector(IWrappedTokenGatewayV3.withdrawETH.selector),
        abi.encode("not enough liquidity")
    );

    vm.expectRevert();
    withdrawalManager.completeWithdrawal(LRTConstants.ETH_TOKEN, "");

    vm.prank(pauser);
    vm.expectRevert();
    withdrawalManager.emergencyWithdrawFromAave(type(uint256).max);

    vm.prank(manager);
    vm.expectRevert();
    withdrawalManager.setAaveIntegrationEnabled(false);

    // isAaveIntegrationEnabled is still true; no recovery path exists
    assertTrue(withdrawalManager.isAaveIntegrationEnabled());
}
```