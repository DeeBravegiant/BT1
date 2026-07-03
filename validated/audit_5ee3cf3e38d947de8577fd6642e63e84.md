Audit Report

## Title
Aave v3 Pool Pause Causes Unrecoverable DOS of `completeWithdrawal` for ETH Withdrawers - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`LRTWithdrawalManager._processWithdrawalCompletion` unconditionally calls `_withdrawFromAave` when the contract's ETH balance is insufficient to cover a user's withdrawal. `_withdrawFromAave` calls `aaveWETHGateway.withdrawETH` with no error handling. When the Aave v3 WETH pool is paused, this call reverts, permanently blocking `completeWithdrawal` for all ETH withdrawers whose funds are held in Aave — after their rsETH has already been burned. No admin escape hatch exists: every admin recovery path also calls `_withdrawFromAave` and reverts identically.

## Finding Description

The deposit path in `unlockQueue` wraps `depositToAaveExternal` in a `try/catch` (line 311), silently continuing on failure. The withdrawal path in `_processWithdrawalCompletion` (lines 720–724) has no equivalent protection:

```solidity
if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
    uint256 contractBalance = address(this).balance;
    if (contractBalance < request.expectedAssetAmount) {
        uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
        _withdrawFromAave(amountNeeded);   // no try/catch
```

`_withdrawFromAave` (line 917) calls:

```solidity
aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this));
```

When the Aave v3 pool is paused, `withdrawETH` reverts unconditionally, propagating through `completeWithdrawal`. The rsETH was already burned by `unlockQueue` at line 305 (`IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned)`), so affected users have no rsETH to reclaim and no ETH to receive.

**All admin escape hatches are equally broken while Aave is paused:**
- `emergencyWithdrawFromAave` (line 560): calls `_withdrawFromAave(amount)` → reverts.
- `setAaveIntegrationEnabled(false)` (line 495): calls `_withdrawFromAave(aaveBalance)` before setting the flag → reverts.
- `configureAaveIntegration` (line 447): calls `_withdrawFromAave(aaveBalance)` → reverts.

There is no code path that bypasses `_withdrawFromAave` to pay users from an alternative source or to force-disable the Aave integration while the pool is paused.

## Impact Explanation

**Medium — Temporary freezing of funds** (escalating to Critical — Permanent freezing of funds if the Aave market is deprecated or paused indefinitely).

Users whose rsETH has been burned by `unlockQueue` cannot complete ETH withdrawals for the duration of the Aave pause. Their rsETH is gone and their ETH is locked in Aave with no protocol-level fallback. The absence of any admin escape hatch means the protocol cannot unilaterally resolve the situation; recovery depends entirely on Aave unpausing.

## Likelihood Explanation

Aave v3 has a well-documented pool-level and reserve-level pause mechanism. The Aave Guardian (a multisig) can pause pools without a full governance vote. The WETH market on Aave v3 Ethereum has been subject to emergency pauses historically (e.g., during the Euler hack contagion period in March 2023). The Aave integration, once enabled, routes all unlocked ETH into Aave, making every pending ETH withdrawal dependent on Aave availability. No attacker action is required; the precondition is a normal Aave operational event.

## Recommendation

1. **Immediate fix**: Wrap the `_withdrawFromAave` call inside `_processWithdrawalCompletion` in a `try/catch`. On failure, pay the user from whatever native ETH balance is available and record the shortfall for later settlement.
2. **Admin escape hatch**: Add a separate admin function (e.g., `forceDisableAaveIntegration`) that sets `isAaveIntegrationEnabled = false` **without** calling `_withdrawFromAave`, so the protocol can degrade gracefully when Aave is paused and resume paying users once ETH is manually recovered.
3. **Consistency**: Apply the same `try/catch` pattern used in the deposit path (line 311) to all withdrawal-side calls to `_withdrawFromAave`.

## Proof of Concept

1. Aave integration is enabled; operator calls `unlockQueue` for ETH — rsETH is burned (line 305), ETH is deposited to Aave (line 311).
2. Aave governance/Guardian pauses the WETH pool (documented, historical event).
3. User calls `completeWithdrawal(ETH_TOKEN, ...)`.
4. `_processWithdrawalCompletion` sees `address(this).balance < request.expectedAssetAmount` and calls `_withdrawFromAave(amountNeeded)` (line 724).
5. `_withdrawFromAave` calls `aaveWETHGateway.withdrawETH(aavePool, withdrawnAmount, address(this))` (line 917).
6. Aave's pool reverts because the pool is paused.
7. The entire `completeWithdrawal` transaction reverts.
8. User's rsETH is already burned; they cannot recover ETH or rsETH.
9. Admin calls `emergencyWithdrawFromAave` (line 560) → same revert. Admin calls `setAaveIntegrationEnabled(false)` (line 495) → same revert. No recovery path exists.

**Foundry fork test plan**: Fork Ethereum mainnet with Aave v3 active. Deploy/configure `LRTWithdrawalManager` with Aave integration enabled. Call `unlockQueue` to burn rsETH and deposit ETH to Aave. Use `vm.prank(aaveGuardian)` to call `IPool(aavePool).setReserveActive(WETH, false)` or the equivalent pause function. Then call `completeWithdrawal` and assert it reverts. Confirm `emergencyWithdrawFromAave` and `setAaveIntegrationEnabled(false)` also revert.