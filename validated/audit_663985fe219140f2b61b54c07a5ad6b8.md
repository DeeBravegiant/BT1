Audit Report

## Title
`whenNotPaused` on `completeWithdrawal` Freezes Already-Unlocked User Funds During Auto-Pause - (File: contracts/LRTWithdrawalManager.sol)

## Summary
After `unlockQueue()` burns a user's rsETH and pulls the corresponding assets from `LRTUnstakingVault` into `LRTWithdrawalManager`, a subsequent auto-pause triggered by `LRTOracle._updateRsETHPrice()` on a price drop makes those assets inaccessible. `completeWithdrawal` and `completeWithdrawalForUser` both carry `whenNotPaused` guards, so users whose rsETH has already been irreversibly destroyed cannot retrieve the assets owed to them until an admin manually unpauses.

## Finding Description
The withdrawal lifecycle has three phases. In phase 2, `unlockQueue()` atomically burns the queued rsETH from the contract and redeems the corresponding asset amount from the vault:

```solidity
// LRTWithdrawalManager.sol:305-307
if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
unstakingVault.redeem(asset, assetAmountUnlocked);
```

After this point, the user's rsETH is gone and the owed assets sit inside `LRTWithdrawalManager`. Phase 3, `completeWithdrawal`, is guarded by `whenNotPaused`:

```solidity
// LRTWithdrawalManager.sol:183
function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused { ... }
```

`LRTOracle._updateRsETHPrice()` contains an automatic downside-protection mechanism that pauses `LRTWithdrawalManager` without any admin action whenever the rsETH price drops beyond `pricePercentageLimit` relative to `highestRsethPrice`:

```solidity
// LRTOracle.sol:277-281
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
```

`updateRSETHPrice()` is a public function callable by anyone. If it fires after `unlockQueue()` has settled a batch of requests, every affected user's `completeWithdrawal()` call reverts unconditionally. The operator-assisted path `completeWithdrawalForUser` carries the same `whenNotPaused` guard (line 199) and is equally blocked. There is no alternative claim path for users.

## Impact Explanation
**Medium — Temporary (potentially indefinite) freezing of funds.**

After `unlockQueue()` executes, the user's rsETH is irreversibly burned and the owed assets are held inside `LRTWithdrawalManager`. While the contract is paused, `completeWithdrawal` reverts unconditionally, so those assets cannot reach their owners. The freeze lasts until an admin with `DEFAULT_ADMIN_ROLE` calls `unpause()`. If the pause is triggered by a severe slashing or depeg event, the admin may delay unpausing indefinitely. Users have no self-service escape path. This matches the allowed impact: **Medium. Temporary freezing of funds.**

## Likelihood Explanation
**Medium.** Two realistic conditions must coincide: (1) one or more withdrawal requests have been processed through `unlockQueue()` — assets in manager, rsETH burned — but not yet claimed; and (2) `LRTOracle.updateRSETHPrice()` is called (a routine, public, permissionless call) while the rsETH price has dropped beyond `pricePercentageLimit` relative to `highestRsethPrice`. A significant EigenLayer slashing event or LST depeg is a realistic trigger for the price-drop condition. Both conditions can occur simultaneously without any attacker involvement and without any privileged action.

## Recommendation
Remove `whenNotPaused` from `completeWithdrawal` and `completeWithdrawalForUser`. By the time these functions are called, the user's rsETH has already been burned and the assets are already inside the manager — there is no economic reason to block the final transfer. Alternatively, introduce a separate `claimPaused` flag that blocks only new `initiateWithdrawal` requests while still allowing already-unlocked claims to proceed.

## Proof of Concept
1. Alice calls `initiateWithdrawal(ETH, 1e18 rsETH, "")`. Her rsETH is transferred to `LRTWithdrawalManager`.
2. An operator calls `unlockQueue(ETH, ...)`. At line 305, Alice's rsETH is burned via `burnFrom`. At line 307, ETH is pulled from `LRTUnstakingVault` into `LRTWithdrawalManager`. Alice's request is now marked unlocked.
3. Anyone calls `LRTOracle.updateRSETHPrice()`. The new rsETH price is below `highestRsethPrice` by more than `pricePercentageLimit`. Line 279 executes: `withdrawalManager.pause()`.
4. Alice calls `completeWithdrawal(ETH, "")`. The `whenNotPaused` modifier at line 183 reverts the call.
5. Alice's ETH sits in `LRTWithdrawalManager`. Her rsETH is gone. She has no recourse until an admin calls `unpause()`.