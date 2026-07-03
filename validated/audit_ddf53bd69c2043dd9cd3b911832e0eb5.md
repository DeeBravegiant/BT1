Audit Report

## Title
Aave WETH Pool Illiquidity Permanently Blocks ETH Withdrawals With No Functional Admin Override — (`contracts/LRTWithdrawalManager.sol`)

## Summary
When `isAaveIntegrationEnabled` is `true` and the Aave WETH pool reaches 100% utilization, every call to `completeWithdrawal` for ETH reverts because `_withdrawFromAave` propagates the Aave revert with no error handling. All three admin override paths (`emergencyWithdrawFromAave`, `setAaveIntegrationEnabled(false)`, `configureAaveIntegration` reconfiguration) share the same unguarded `_withdrawFromAave` call and revert under identical conditions, leaving the protocol with no on-chain mechanism to unblock user withdrawals until Aave liquidity recovers organically or a governance upgrade is executed.

## Finding Description
`_processWithdrawalCompletion` unconditionally calls `_withdrawFromAave` when `isAaveIntegrationEnabled && asset == ETH_TOKEN` and the contract balance is insufficient:

```solidity
// LRTWithdrawalManager.sol L720-724
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);   // no try/catch
```

`_withdrawFromAave` calls the Aave gateway with no error handling:

```solidity
// LRTWithdrawalManager.sol L917
aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this));
```

When Aave WETH utilization is 100%, `withdrawETH` reverts (Aave error `35` — `UNDERLYING_BALANCE_ZERO`). The revert propagates through `_processWithdrawalCompletion` → `completeWithdrawal`, blocking all ETH withdrawals. The `popFront()` and `delete withdrawalRequests[requestId]` executed earlier (lines 705, 712) are rolled back, so requests are not lost but cannot be fulfilled.

All three admin escape hatches are equally broken:

- `emergencyWithdrawFromAave` (line 560): calls `_withdrawFromAave(amount)` directly with no try/catch.
- `setAaveIntegrationEnabled(false)` (lines 494–495): calls `_withdrawFromAave(aaveBalance)` **before** setting `isAaveIntegrationEnabled = false` at line 503. If the call reverts, the flag is never updated.
- `configureAaveIntegration` reconfiguration (line 447): calls `_withdrawFromAave(aaveBalance)` before updating addresses. Same revert path.

There is no code path that sets `isAaveIntegrationEnabled = false` without first successfully withdrawing from Aave.

## Impact Explanation
**Medium — Temporary freezing of funds.**

All ETH withdrawal completions are blocked for the entire duration of Aave WETH pool illiquidity. The protocol has no functional on-chain mechanism to override this state: the emergency withdrawal, the integration-disable path, and the reconfiguration path all share the same broken `_withdrawFromAave` call. The only resolution is Aave liquidity recovering organically or a contract upgrade via governance. This is a concrete, prolonged temporary freeze with no admin override, matching the allowed impact class "Medium. Temporary freezing of funds."

## Likelihood Explanation
The protocol actively deposits idle ETH into Aave via `unlockQueue`. Any non-trivial Aave balance combined with a high-utilization event triggers this path. Aave v3 WETH on mainnet has historically reached very high utilization during periods of elevated ETH borrowing demand (e.g., around the Merge, liquid staking yield spikes). Sustained 100% utilization is uncommon but is a realistic and documented market condition. No attacker action is required — the condition arises from normal market dynamics, and any user calling `completeWithdrawal` for ETH will trigger the revert.

## Recommendation
1. Wrap the `_withdrawFromAave` call inside `_processWithdrawalCompletion` in a `try/catch` and revert with a clear `InsufficientLiquidityForWithdrawal` error rather than an opaque Aave revert.
2. More critically, add a force-disable path in `setAaveIntegrationEnabled(false)` that sets `isAaveIntegrationEnabled = false` **unconditionally first**, then attempts the Aave withdrawal as best-effort inside a `try/catch`. This ensures the integration can always be disabled regardless of Aave pool state, immediately unblocking `completeWithdrawal` since the Aave branch is only entered when `isAaveIntegrationEnabled == true`.
3. Apply the same try/catch pattern to `emergencyWithdrawFromAave` and `configureAaveIntegration` reconfiguration.

## Proof of Concept
```solidity
// 1. Fork mainnet
// 2. Set Aave WETH utilization to ~100% by borrowing all available WETH
// 3. Ensure withdrawalManager has a non-zero aaveAWETH balance

// User attempts withdrawal — reverts
vm.prank(user);
withdrawalManager.completeWithdrawal(ETH_TOKEN, "ref");
// → reverts with Aave error (UNDERLYING_BALANCE_ZERO)

// Admin attempts to disable integration — also reverts
vm.prank(manager);
withdrawalManager.setAaveIntegrationEnabled(false);
// → reverts at _withdrawFromAave (line 495), isAaveIntegrationEnabled remains true

// Pauser attempts emergency withdrawal — also reverts
vm.prank(pauser);
withdrawalManager.emergencyWithdrawFromAave(type(uint256).max);
// → reverts at _withdrawFromAave (line 560)

// No on-chain path exists to unblock withdrawals
// All three escape hatches confirmed broken
```