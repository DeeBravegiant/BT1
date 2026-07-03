Audit Report

## Title
Aave WETH Liquidity Exhaustion Blocks All ETH `completeWithdrawal` Calls With No Working Escape Hatch — (`contracts/LRTWithdrawalManager.sol`)

## Summary
When `isAaveIntegrationEnabled` is `true` and the Aave WETH pool reaches 100% utilization, `_withdrawFromAave` reverts on the bare `aaveWETHGateway.withdrawETH` call at line 917. This revert propagates uncaught through `_processWithdrawalCompletion` (line 724), blocking every ETH `completeWithdrawal` call. All three administrative escape hatches — `emergencyWithdrawFromAave`, `setAaveIntegrationEnabled(false)`, and `configureAaveIntegration` — also invoke `_withdrawFromAave` without error handling, leaving no recovery path until Aave liquidity naturally recovers.

## Finding Description
**Root cause:** `_withdrawFromAave` makes a bare external call to `aaveWETHGateway.withdrawETH` at line 917 with no try/catch or error handling. Any revert from Aave propagates directly to all callers.

**Withdrawal path:** In `_processWithdrawalCompletion` (lines 720–731), when `address(this).balance < request.expectedAssetAmount`, the code calls `_withdrawFromAave(amountNeeded)` at line 724 with no error handling. If Aave reverts, the entire `completeWithdrawal` call reverts for every user with a pending unlocked ETH request.

**Contrast with deposit path:** The `unlockQueue` deposit path at lines 311–316 correctly wraps `depositToAaveExternal` in a try/catch, silently failing and emitting `AaveDepositFailed`. No equivalent protection exists on the withdrawal side.

**All escape hatches fail under the same condition:**
- `emergencyWithdrawFromAave` (PAUSER_ROLE): calls `_withdrawFromAave(amount)` at line 560 — reverts.
- `setAaveIntegrationEnabled(false)` (LRT Manager): calls `_withdrawFromAave(aaveBalance)` at line 495 — reverts, so `isAaveIntegrationEnabled` is never set to `false`.
- `configureAaveIntegration` (LRT Manager): calls `_withdrawFromAave(aaveBalance)` at line 447 — reverts.

None of these wrap `_withdrawFromAave` in a try/catch or provide a force-skip path. The manager cannot set `isAaveIntegrationEnabled = false` without first successfully draining Aave, which is impossible at 100% utilization.

## Impact Explanation
**Medium — Temporary freezing of funds.** All ETH `completeWithdrawal` calls revert for every user with a pending unlocked ETH withdrawal request for the entire duration that Aave WETH utilization remains at or near 100%. The aWETH balance is intact (funds are not lost), but they are inaccessible to users. The freeze duration is entirely outside the protocol's control, as no administrative action can unblock withdrawals while the condition persists.

## Likelihood Explanation
The preconditions are all reachable in normal operation: `isAaveIntegrationEnabled = true` is the intended production state; the contract's ETH balance being below a withdrawal amount is expected since the protocol actively deposits unlocked ETH into Aave via `unlockQueue`; and Aave WETH utilization reaching 100% is a historically observed market condition during high ETH demand or market stress. No attacker action is required — the condition arises from normal market dynamics. Any user with an unlocked ETH withdrawal request is affected.

## Recommendation
1. **Wrap `_withdrawFromAave` in `_processWithdrawalCompletion` with a try/catch.** If the Aave withdrawal fails, fall through to the existing `InsufficientLiquidityForWithdrawal` revert at line 729 rather than propagating the opaque Aave revert. This preserves existing error semantics.
2. **Decouple `setAaveIntegrationEnabled(false)` from `_withdrawFromAave`.** Allow the manager to set `isAaveIntegrationEnabled = false` even when Aave cannot be drained — skip the withdrawal if Aave reverts, leaving aWETH in place to be claimed later via a separate recovery function.
3. **Add a `forceDisableAaveIntegration` function** (restricted to PAUSER_ROLE or LRT Manager) that sets `isAaveIntegrationEnabled = false` without attempting to withdraw, for use when Aave is illiquid.

## Proof of Concept
```solidity
// Fork mainnet with Aave WETH at 100% utilization
// 1. Enable Aave integration; deposit ETH to Aave via unlockQueue (normal operation)
// 2. Simulate Aave WETH pool at 100% utilization (all WETH borrowed)
// 3. Call completeWithdrawal for an unlocked ETH request
//    where address(this).balance < request.expectedAssetAmount
//    → _withdrawFromAave called at line 724
//    → aaveWETHGateway.withdrawETH reverts at line 917
//    → completeWithdrawal reverts for ALL users
// 4. Call emergencyWithdrawFromAave(type(uint256).max)
//    → _withdrawFromAave called at line 560 → reverts
// 5. Call setAaveIntegrationEnabled(false)
//    → _withdrawFromAave called at line 495 → reverts
//    → isAaveIntegrationEnabled remains true
// Result: no administrative action can unblock withdrawals

function testAaveLiquidityExhaustionBlocksWithdrawals() public {
    // Setup: fork mainnet, configure Aave integration, unlock ETH withdrawal
    vm.mockCallRevert(
        address(aaveWETHGateway),
        abi.encodeWithSelector(IWrappedTokenGatewayV3.withdrawETH.selector),
        abi.encode("not enough liquidity")
    );
    vm.expectRevert();
    withdrawalManager.completeWithdrawal(LRTConstants.ETH_TOKEN, "");

    vm.expectRevert();
    withdrawalManager.emergencyWithdrawFromAave(type(uint256).max);

    vm.expectRevert();
    withdrawalManager.setAaveIntegrationEnabled(false);
    // isAaveIntegrationEnabled is still true — no escape hatch available
}
```