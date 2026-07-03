Audit Report

## Title
`instantWithdrawal` Ignores `assetsCommitted`, Allowing Drain of Assets Reserved for Queued Withdrawal Requests - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`instantWithdrawal` checks only `unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)`, which subtracts the manually-set `queuedWithdrawalsBuffer` from the vault balance. Because `queuedWithdrawalsBuffer` defaults to zero and is never automatically updated when `initiateWithdrawal` is called, an unprivileged rsETH holder can drain the entire `LRTUnstakingVault` balance even when 100% of it is already committed to pending queued withdrawal requests, freezing those users' funds.

## Finding Description

When `initiateWithdrawal` is called, the contract enforces that the new commitment does not exceed available assets:

```solidity
// LRTWithdrawalManager.sol L168-173
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
assetsCommitted[asset] += expectedAssetAmount;
```

`getAvailableAssetAmount` computes `getTotalAssetDeposits(asset) - assetsCommitted[asset]`, and `getTotalAssetDeposits` includes `assetLyingUnstakingVault` (the vault balance), so vault assets are counted as backing the commitment.

`instantWithdrawal` performs a completely separate check:

```solidity
// LRTWithdrawalManager.sol L231-233
if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
    revert CantInstantWithdrawMoreThanAvailable();
}
```

`getAssetsAvailableForInstantWithdrawal` returns `vaultBalance - queuedWithdrawalsBuffer[asset]`. Since `queuedWithdrawalsBuffer` is zero by default and is only updated via `setQueuedWithdrawalsBuffer` (callable only by `onlyLRTOperator`), the entire vault balance is reported as available for instant withdrawal regardless of how much is committed. `instantWithdrawal` never reads `assetsCommitted`.

After the drain, `unlockQueue` calls `_createUnlockParams`, which sets `totalAvailableAssets = unstakingVault.balanceOf(asset)`. With the vault empty, this is zero, and the function reverts at:

```solidity
// LRTWithdrawalManager.sol L297
if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();
```

Alice's rsETH (already transferred to the WithdrawalManager at `initiateWithdrawal` line 166) is frozen until the vault is manually replenished.

## Impact Explanation

**Medium — Temporary freezing of funds.** Users who have called `initiateWithdrawal` have already transferred their rsETH to the WithdrawalManager. If an attacker drains the vault via `instantWithdrawal`, those users cannot complete their withdrawals until the operator replenishes the vault (e.g., by completing an EigenLayer withdrawal cycle, which carries a multi-day delay). If no other protocol assets exist at that moment, the freeze extends until the operator takes corrective action.

## Likelihood Explanation

- Instant withdrawals are enabled by `setInstantWithdrawalEnabled` (LRT Manager), a routine operational action.
- `queuedWithdrawalsBuffer` is zero by default; many deployments will leave it unset.
- No special privilege is required — any rsETH holder can call `instantWithdrawal`.
- The attacker does not lose funds; they redeem rsETH at the current oracle rate, which is always available to them.
- The attack is repeatable any time instant withdrawals are enabled and the buffer is unset.

## Recommendation

1. **Automatic buffer accounting**: When `initiateWithdrawal` increments `assetsCommitted[asset]`, also increase `queuedWithdrawalsBuffer[asset]` in the unstaking vault by the same amount; decrease it when the request is unlocked (`_unlockWithdrawalRequests` already decrements `assetsCommitted` at line 802) or cancelled.
2. **Cross-check in `instantWithdrawal`**: Before redeeming from the vault, verify `assetAmountUnlocked <= unstakingVault.balanceOf(asset) - assetsCommitted[asset]`, analogous to how `getAvailableAssetAmount` protects `initiateWithdrawal`.

## Proof of Concept

```
State: LRTUnstakingVault holds 100 ETH, deposit pool holds 0 ETH.
       queuedWithdrawalsBuffer[ETH] = 0 (default).
       Instant withdrawals enabled for ETH.

1. Alice calls initiateWithdrawal(ETH, rsETHAmount_A) where rsETHAmount_A maps to 100 ETH:
   - rsETH transferred to WithdrawalManager (line 166).
   - getAvailableAssetAmount(ETH) = 100 - 0 = 100 → passes.
   - assetsCommitted[ETH] += 100 → assetsCommitted[ETH] = 100.

2. Bob calls instantWithdrawal(ETH, rsETHAmount_B) where rsETHAmount_B maps to 100 ETH:
   - assetAmountUnlocked = 100 ETH.
   - getAssetsAvailableForInstantWithdrawal(ETH) = 100 - 0 = 100 → passes (line 231).
   - assetsCommitted[ETH] is never consulted.
   - Bob burns rsETH, receives 100 ETH from vault (lines 229, 235).
   - LRTUnstakingVault balance = 0.

3. Operator calls unlockQueue(ETH, ...):
   - totalAvailableAssets = unstakingVault.balanceOf(ETH) = 0.
   - Reverts: AmountMustBeGreaterThanZero (line 297).
   - Alice's rsETH remains locked in WithdrawalManager indefinitely.
```

Foundry test plan: deploy the full stack with a fork, seed the vault with 100 ETH, call `initiateWithdrawal` as Alice, then call `instantWithdrawal` as Bob for the same amount, then assert that `unlockQueue` reverts and Alice's withdrawal request remains locked.