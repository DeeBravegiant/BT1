Audit Report

## Title
Disconnected Accounting Between `assetsCommitted` and `queuedWithdrawalsBuffer` Allows Instant Withdrawals to Drain Assets Reserved for Queued Withdrawals — (`contracts/LRTWithdrawalManager.sol` / `contracts/LRTUnstakingVault.sol`)

## Summary
`LRTWithdrawalManager` records queued withdrawal commitments in `assetsCommitted[asset]`, but `LRTUnstakingVault` protects vault assets from instant withdrawals via a separate, manually-set `queuedWithdrawalsBuffer[asset]` that defaults to zero and is never automatically synchronized with `assetsCommitted`. An unprivileged user can call `instantWithdrawal` and drain vault assets already committed to pending queued withdrawals, leaving those queued withdrawal users unable to complete their withdrawals until the vault is externally replenished.

## Finding Description
When a user calls `initiateWithdrawal`, the contract records the committed amount at `LRTWithdrawalManager.sol:173`:
```solidity
assetsCommitted[asset] += expectedAssetAmount;
```
The over-commitment guard (`getAvailableAssetAmount`, lines 599–603) correctly subtracts `assetsCommitted` from total protocol assets, preventing double-queuing. However, `instantWithdrawal` (lines 231–235) uses a completely independent check:
```solidity
if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
    revert CantInstantWithdrawMoreThanAvailable();
}
unstakingVault.redeem(asset, assetAmountUnlocked);
```
`getAssetsAvailableForInstantWithdrawal` (LRTUnstakingVault.sol lines 235–237) knows nothing about `assetsCommitted`:
```solidity
uint256 reservedBuffer = queuedWithdrawalsBuffer[asset];
availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
```
`queuedWithdrawalsBuffer` is a storage mapping (default zero) set only by an explicit operator call (`setQueuedWithdrawalsBuffer`, lines 199–208). There is no protocol-level enforcement that it be updated when `assetsCommitted` changes. With `queuedWithdrawalsBuffer == 0`, `getAssetsAvailableForInstantWithdrawal` returns the full vault balance, allowing instant withdrawals to consume assets already committed to queued requests. Subsequently, `unlockQueue` reads `totalAvailableAssets: unstakingVault.balanceOf(asset)` (line 849), which returns zero after the drain, so `_unlockWithdrawalRequests` cannot service the queued request. The queued user's rsETH was already transferred to the contract at line 166 and cannot be reclaimed.

## Impact Explanation
**Medium — Temporary freezing of funds.** A queued withdrawal user's rsETH is held by the contract and their withdrawal request cannot be unlocked until the vault is replenished from NodeDelegators or EigenLayer. EigenLayer's multi-day withdrawal delay makes this a prolonged but ultimately temporary freeze. This matches the allowed impact "Medium. Temporary freezing of funds."

## Likelihood Explanation
`isInstantWithdrawalEnabled` is set by `onlyLRTManager` as a normal operational action (lines 360–366). `queuedWithdrawalsBuffer` defaults to zero for every asset and requires a separate, explicit operator call to set — there is no protocol enforcement linking it to `assetsCommitted`. Once instant withdrawal is enabled with a zero buffer (the default state), any unprivileged external user can call `instantWithdrawal` to drain vault assets committed to queued withdrawals. No privileged collusion, oracle manipulation, or unrealistic assumptions are required.

## Recommendation
Replace the manually-maintained `queuedWithdrawalsBuffer` with a dynamic read of `assetsCommitted` from `LRTWithdrawalManager`. `getAssetsAvailableForInstantWithdrawal` should compute:
```solidity
uint256 committed = ILRTWithdrawalManager(withdrawalManager).assetsCommitted(asset);
uint256 unprotected = vaultBalance > committed ? vaultBalance - committed : 0;
```
and return `unprotected` instead of `vaultBalance - queuedWithdrawalsBuffer`. This eliminates the manual synchronization requirement and ensures the two accounting systems are always consistent.

## Proof of Concept
1. Manager calls `setInstantWithdrawalEnabled(asset, true)`. `queuedWithdrawalsBuffer[asset]` remains `0` (storage default).
2. User A calls `initiateWithdrawal(asset, rsETHAmount, ...)`. rsETH is transferred from User A to the contract (line 166); `assetsCommitted[asset] += X` (line 173). Vault holds at least `X` of `asset`.
3. User B calls `instantWithdrawal(asset, rsETHAmount2, ...)` where `rsETHAmount2` corresponds to `X` assets. `getAssetsAvailableForInstantWithdrawal(asset)` returns `vaultBalance - 0 = X`. Check passes; `unstakingVault.redeem(asset, X)` drains the vault (line 235).
4. Operator calls `unlockQueue(asset, ...)`. `_createUnlockParams` reads `unstakingVault.balanceOf(asset) == 0` (line 849). `unlockQueue` reverts at line 297 (`AmountMustBeGreaterThanZero`) or processes zero assets, leaving User A's request locked.
5. User A cannot call `completeWithdrawal` because their request was never unlocked. Funds remain frozen until the operator manually replenishes the vault from EigenLayer (subject to EigenLayer's multi-day withdrawal delay).