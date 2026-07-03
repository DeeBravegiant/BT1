Audit Report

## Title
Aave v3 Pool Pause Causes Permanent DOS of `completeWithdrawal` for ETH Withdrawers - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`LRTWithdrawalManager._processWithdrawalCompletion` unconditionally calls `_withdrawFromAave` when the contract's ETH balance is insufficient to cover a user's withdrawal. Because `_withdrawFromAave` calls `aaveWETHGateway.withdrawETH` with no error handling, a paused Aave v3 WETH pool causes every `completeWithdrawal` call for ETH to revert. All admin escape hatches (`emergencyWithdrawFromAave`, `setAaveIntegrationEnabled(false)`) also route through `_withdrawFromAave` and are equally blocked, leaving users with burned rsETH and no path to recover their ETH.

## Finding Description

The deposit path in `unlockQueue` (line 311) wraps `depositToAaveExternal` in a `try/catch`, explicitly tolerating Aave failures:

```solidity
// L309-316
try this.depositToAaveExternal(assetAmountUnlocked) { }
catch (bytes memory reason) {
    emit AaveDepositFailed(assetAmountUnlocked, reason);
}
```

The withdrawal path in `_processWithdrawalCompletion` (lines 719–731) has no equivalent protection:

```solidity
// L720-731
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);   // no try/catch
        ...
    }
}
```

`_withdrawFromAave` (line 917) calls:

```solidity
aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this));
```

Aave v3 enforces a pool-level pause that causes `withdrawETH` to revert unconditionally. The revert propagates through `completeWithdrawal`, blocking all ETH withdrawers whose funds are held in Aave.

All three admin escape hatches also call `_withdrawFromAave` without protection:
- `setAaveIntegrationEnabled(false)` — lines 486–497
- `emergencyWithdrawFromAave` — lines 551–563
- `configureAaveIntegration` — lines 438–453

There is no code path that bypasses Aave and pays users from an alternative source while the pool is paused.

## Impact Explanation

**Medium — Temporary freezing of funds.** Users whose rsETH was already burned by `unlockQueue` cannot complete ETH withdrawals for the duration of the Aave pause. Their rsETH is gone and their ETH is locked in Aave with no protocol-level fallback. If the pause is extended or the market is deprecated, this escalates to a permanent freeze.

## Likelihood Explanation

Aave v3 has a well-documented pool-level and reserve-level pause mechanism exercised by Aave governance and the Aave Guardian. The WETH market on Aave v3 Ethereum is one of the largest markets and has been subject to emergency pauses historically. The Aave integration, once enabled, routes all unlocked ETH into Aave, making every ETH withdrawer dependent on Aave liveness. The precondition (Aave pause) is a documented, realistic external event — not a protocol compromise — and the deposit path's own `try/catch` demonstrates the developers were already aware Aave can fail.

## Recommendation

1. Wrap the `_withdrawFromAave` call inside `_processWithdrawalCompletion` in a `try/catch`. On failure, pay the user from whatever native ETH balance is available and record the shortfall for later settlement.
2. Add a separate admin function that force-sets `isAaveIntegrationEnabled = false` **without** attempting to withdraw from Aave first, so the protocol can degrade gracefully and users can be paid once ETH is manually recovered.

## Proof of Concept

1. Aave integration is enabled; ETH is deposited to Aave via `unlockQueue` (line 311).
2. Aave governance pauses the WETH pool (documented, realistic event).
3. User calls `completeWithdrawal(ETH_TOKEN, ...)`.
4. `_processWithdrawalCompletion` (line 722) sees `address(this).balance < request.expectedAssetAmount` and calls `_withdrawFromAave` (line 724).
5. `_withdrawFromAave` calls `aaveWETHGateway.withdrawETH(aavePool, ...)` at line 917.
6. Aave's pool reverts because the pool is paused.
7. The entire `completeWithdrawal` transaction reverts.
8. The user's rsETH is already burned. They cannot recover their ETH.
9. `emergencyWithdrawFromAave` (line 560) and `setAaveIntegrationEnabled(false)` (line 495) both also call `_withdrawFromAave` and also revert — no admin escape hatch is available.

**Foundry fork test plan:** Fork Ethereum mainnet with Aave v3 active. Deploy/configure `LRTWithdrawalManager` with Aave integration enabled. Call `unlockQueue` to burn rsETH and deposit ETH to Aave. Impersonate the Aave Guardian and call `pool.setReservePause(WETH, true)`. Call `completeWithdrawal` and assert it reverts. Then call `emergencyWithdrawFromAave` and `setAaveIntegrationEnabled(false)` and assert both also revert, confirming no admin escape hatch is available.