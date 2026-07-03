Audit Report

## Title
Aave Pool Pause Causes Unrecoverable DOS of `completeWithdrawal` for ETH Withdrawers After rsETH Burn - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`LRTWithdrawalManager._processWithdrawalCompletion` unconditionally calls `_withdrawFromAave` when the contract's ETH balance is insufficient to cover a withdrawal, with no error handling. When the Aave v3 WETH pool is paused, `aaveWETHGateway.withdrawETH` reverts, causing `completeWithdrawal` to revert for all ETH withdrawers whose funds are in Aave. Critically, rsETH is already burned at the `unlockQueue` step, so affected users have no rsETH and cannot access their ETH. Every admin escape hatch (`emergencyWithdrawFromAave`, `setAaveIntegrationEnabled(false)`, `configureAaveIntegration`) also calls `_withdrawFromAave` and reverts identically, leaving no protocol-level recovery path.

## Finding Description

The deposit path in `unlockQueue` wraps `depositToAaveExternal` in a `try/catch` (L311), silently continuing on failure. The withdrawal path in `_processWithdrawalCompletion` (L724) calls `_withdrawFromAave` with no equivalent protection:

```solidity
// L719-731
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);   // no try/catch
        ...
    }
}
```

`_withdrawFromAave` (L917) calls:
```solidity
aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this));
```

When the Aave v3 pool is paused, this call reverts unconditionally. The revert propagates through `completeWithdrawal`.

All three admin escape hatches also call `_withdrawFromAave` without protection:
- `setAaveIntegrationEnabled(false)` at L495
- `emergencyWithdrawFromAave` at L560
- `configureAaveIntegration` at L447

There is no code path that bypasses `_withdrawFromAave` to pay users from an alternative source or to force-disable the Aave integration without first withdrawing.

## Impact Explanation

**Medium — Temporary freezing of funds.** Users whose rsETH was burned by `unlockQueue` cannot complete ETH withdrawals for the duration of the Aave pause. Their rsETH is gone and their ETH is locked in Aave with no protocol-level fallback. If the pause is extended or the market is deprecated, this escalates to permanent freezing.

## Likelihood Explanation

Aave v3 has a well-documented pool-level and reserve-level pause mechanism exercised by Aave governance and the Aave Guardian. The WETH market on Aave v3 Ethereum has been subject to emergency pauses in the past. The Aave integration is opt-in but, once enabled, routes all unlocked ETH into Aave, making every ETH withdrawer dependent on Aave liveness. No attacker action is required — the pause is a normal Aave governance event, and any user calling `completeWithdrawal` during a pause triggers the DOS.

## Recommendation

1. Wrap the `_withdrawFromAave` call in `_processWithdrawalCompletion` in a `try/catch`. On failure, pay the user from whatever native ETH balance is available and record the shortfall for later settlement.
2. Add a separate admin function that force-sets `isAaveIntegrationEnabled = false` **without** calling `_withdrawFromAave`, so the protocol can degrade gracefully when Aave is paused and resume paying users once ETH is manually recovered.

## Proof of Concept

1. Aave integration is enabled; operator calls `unlockQueue` — rsETH is burned, ETH is deposited to Aave (L305, L311).
2. Aave governance pauses the WETH pool (documented, real-world event).
3. User calls `completeWithdrawal(ETH_TOKEN, ...)`.
4. `_processWithdrawalCompletion` (L699) sees `address(this).balance < request.expectedAssetAmount` and calls `_withdrawFromAave(amountNeeded)` at L724.
5. `_withdrawFromAave` calls `aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this))` at L917.
6. Aave's pool reverts because the pool is paused.
7. The entire `completeWithdrawal` transaction reverts.
8. The user's rsETH is already burned (step 1). They cannot recover their ETH.
9. Admin calls `emergencyWithdrawFromAave` (L560) → also calls `_withdrawFromAave` → also reverts.
10. Admin calls `setAaveIntegrationEnabled(false)` (L495) → also calls `_withdrawFromAave` → also reverts.
11. No recovery path exists while the Aave pool remains paused.