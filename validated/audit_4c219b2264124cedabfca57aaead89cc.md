Audit Report

## Title
`instantWithdrawal` Bypasses `assetsCommitted` Accounting, Enabling Drain of Assets Reserved for Queued Withdrawal Requests - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`instantWithdrawal` checks only `unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)`, which is computed as `vaultBalance - queuedWithdrawalsBuffer[asset]`. Because `queuedWithdrawalsBuffer` defaults to zero and is never automatically updated when `initiateWithdrawal` increments `assetsCommitted`, an unprivileged rsETH holder can drain the entire `LRTUnstakingVault` balance even when 100% of it is already committed to pending queued withdrawal requests. This leaves queued-withdrawal users' rsETH frozen in the `LRTWithdrawalManager` until the operator manually replenishes the vault.

## Finding Description
`initiateWithdrawal` enforces the invariant that new commitments do not exceed available assets:

```solidity
// LRTWithdrawalManager.sol L168-173
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
assetsCommitted[asset] += expectedAssetAmount;
```

`getAvailableAssetAmount` (L599-603) computes `getTotalAssetDeposits(asset) - assetsCommitted[asset]`, where `getTotalAssetDeposits` includes `assetLyingUnstakingVault` (LRTDepositPool.sol L385-396). So vault assets are counted as backing the commitment.

`instantWithdrawal` performs a completely separate check (L231):

```solidity
if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
    revert CantInstantWithdrawMoreThanAvailable();
}
```

`getAssetsAvailableForInstantWithdrawal` (LRTUnstakingVault.sol L235-237) returns:

```solidity
availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
```

`queuedWithdrawalsBuffer` is set exclusively by `onlyLRTOperator` via `setQueuedWithdrawalsBuffer` (LRTUnstakingVault.sol L199-209) and is **never automatically updated** when `initiateWithdrawal` is called. Its default value is `0`.

Because `instantWithdrawal` never reads `assetsCommitted`, with `queuedWithdrawalsBuffer == 0` the entire vault balance is reported as available for instant withdrawal, even if 100% of it is committed to pending queued requests.

When `unlockQueue` is subsequently called, `_createUnlockParams` reads `totalAvailableAssets = unstakingVault.balanceOf(asset)` (L849). With the vault drained, `totalAvailableAssets == 0`, causing `unlockQueue` to revert at L297 (`if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero()`), and queued users' rsETH remains locked in the `LRTWithdrawalManager`.

## Impact Explanation
Queued-withdrawal users' rsETH is frozen in the `LRTWithdrawalManager` contract. The `unlockQueue` function cannot process any pending requests because `unstakingVault.balanceOf(asset) == 0`. Funds remain frozen until the operator manually replenishes the vault (e.g., by completing an EigenLayer withdrawal cycle, which carries a multi-day delay). This constitutes **Temporary freezing of funds — Medium**.

## Likelihood Explanation
- Instant withdrawals must be enabled by the LRT Manager (`setInstantWithdrawalEnabled`), which is a routine operational action.
- `queuedWithdrawalsBuffer` is `0` by default and requires an explicit operator call to set; many deployments will leave it at zero.
- No special privilege is required for the attacker — any holder of rsETH can call `instantWithdrawal`.
- The attacker does not lose funds; they simply redeem rsETH at the current rate.
- The attack is repeatable: after the operator replenishes the vault, the same attacker (or another) can drain it again.

## Recommendation
1. **Automatic buffer accounting**: When `initiateWithdrawal` increments `assetsCommitted[asset]`, also increase `queuedWithdrawalsBuffer[asset]` in the unstaking vault by the same amount; decrease it when the request is unlocked (`_unlockWithdrawalRequests` at L802) or cancelled.
2. **Cross-check in `instantWithdrawal`**: Before redeeming from the vault, verify that `assetAmountUnlocked ≤ unstakingVault.balanceOf(asset) - assetsCommitted[asset]`, analogous to how `getAvailableAssetAmount` protects `initiateWithdrawal`.

## Proof of Concept
```
State:
  LRTUnstakingVault holds 100 ETH.
  LRTDepositPool holds 0 ETH.
  queuedWithdrawalsBuffer[ETH] = 0 (default).
  Instant withdrawals enabled for ETH.

1. Alice calls initiateWithdrawal(ETH, rsETHAmount_A):
   - rsETH transferred to WithdrawalManager.
   - expectedAssetAmount = 100 ETH.
   - getAvailableAssetAmount(ETH) = 100 - 0 = 100 → passes.
   - assetsCommitted[ETH] += 100 → assetsCommitted[ETH] = 100.
   - getAvailableAssetAmount(ETH) = 100 - 100 = 0.

2. Bob calls instantWithdrawal(ETH, rsETHAmount_B) where rsETHAmount_B covers 100 ETH:
   - getAssetsAvailableForInstantWithdrawal(ETH) = 100 - 0 = 100 → passes check.
   - assetsCommitted[ETH] is never consulted.
   - Bob burns rsETH, receives 100 ETH from vault.
   - LRTUnstakingVault balance = 0.

3. Operator calls unlockQueue(ETH, ...):
   - totalAvailableAssets = unstakingVault.balanceOf(ETH) = 0.
   - Reverts with AmountMustBeGreaterThanZero().
   - Alice's rsETH remains locked in the WithdrawalManager indefinitely.
```

Foundry test plan: deploy the full system on a local fork, seed the unstaking vault with ETH, call `initiateWithdrawal` as Alice, call `instantWithdrawal` as Bob to drain the vault, then assert that `unlockQueue` reverts and Alice's withdrawal request remains locked.