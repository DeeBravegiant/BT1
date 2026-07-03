Audit Report

## Title
Static `queuedWithdrawalsBuffer` Allows Instant Withdrawals to Drain Vault Assets Committed to Queued Users - (File: `contracts/LRTUnstakingVault.sol`)

## Summary
`LRTWithdrawalManager` tracks assets promised to queued withdrawal users via `assetsCommitted[asset]`, but `LRTUnstakingVault.getAssetsAvailableForInstantWithdrawal` protects those assets only via a static, manually-maintained `queuedWithdrawalsBuffer` that defaults to `0`. Because the two systems are never automatically synchronized, an unprivileged caller can invoke `instantWithdrawal` and drain vault assets already committed to queued users, temporarily freezing their withdrawals.

## Finding Description

`initiateWithdrawal` reserves an asset amount for a queued user by incrementing `assetsCommitted`:

```solidity
// LRTWithdrawalManager.sol L173
assetsCommitted[asset] += expectedAssetAmount;
``` [1](#0-0) 

`instantWithdrawal` does not consult `assetsCommitted` at all. It checks `getAssetsAvailableForInstantWithdrawal`, which is computed solely from the static `queuedWithdrawalsBuffer`:

```solidity
// LRTWithdrawalManager.sol L231-235
if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
    revert CantInstantWithdrawMoreThanAvailable();
}
unstakingVault.redeem(asset, assetAmountUnlocked);
``` [2](#0-1) 

Inside `LRTUnstakingVault`, the available amount is:

```solidity
// LRTUnstakingVault.sol L235-237
uint256 vaultBalance = balanceOf(asset);
uint256 reservedBuffer = queuedWithdrawalsBuffer[asset];
availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
``` [3](#0-2) 

`queuedWithdrawalsBuffer` defaults to `0` and is only updated by an operator calling `setQueuedWithdrawalsBuffer`: [4](#0-3) 

There is no on-chain mechanism that automatically updates `queuedWithdrawalsBuffer` when `assetsCommitted` grows. When the buffer is `0` (the default), `getAssetsAvailableForInstantWithdrawal` returns the full vault balance, allowing instant withdrawals to consume assets already committed to queued users.

When `unlockQueue` is subsequently called, it reads `totalAvailableAssets` directly from the vault balance: [5](#0-4) 

With the vault drained, `_unlockWithdrawalRequests` exits immediately: [6](#0-5) 

Queued users' rsETH was already transferred to the contract at `initiateWithdrawal` (line 166) and cannot be recovered until the operator manually replenishes the vault from EigenLayer. [7](#0-6) 

## Impact Explanation

Queued withdrawal users who have already transferred their rsETH to the contract via `initiateWithdrawal` cannot complete their withdrawals until the operator manually replenishes the vault. Their funds are temporarily frozen in the protocol. This matches **Medium — Temporary freezing of funds**.

## Likelihood Explanation

- `isInstantWithdrawalEnabled[asset]` must be `true` — a deliberate operator action, but a normal operational state once the feature is live.
- `queuedWithdrawalsBuffer[asset]` defaults to `0`, which is the unsafe state. The operator must proactively set and continuously update it as `assetsCommitted` grows; there is no on-chain enforcement.
- Any unprivileged rsETH holder can call `instantWithdrawal` once enabled.
- The vulnerability window exists continuously between any `initiateWithdrawal` call and the operator's next buffer update.

Likelihood is **Medium**.

## Recommendation

Replace the static `queuedWithdrawalsBuffer` with a dynamic check against `assetsCommitted` inside `LRTUnstakingVault.getAssetsAvailableForInstantWithdrawal`. The vault should query the withdrawal manager's `assetsCommitted[asset]` and subtract it from the vault balance:

```solidity
function getAssetsAvailableForInstantWithdrawal(address asset) external view returns (uint256 availableAmount) {
    uint256 vaultBalance = balanceOf(asset);
    uint256 committed = ILRTWithdrawalManager(withdrawalManager).assetsCommitted(asset);
    availableAmount = committed >= vaultBalance ? 0 : vaultBalance - committed;
}
```

This eliminates the need for a manually maintained buffer and ensures instant withdrawals can never consume assets already committed to queued users.

## Proof of Concept

1. Operator calls `setInstantWithdrawalEnabled(ETH, true)`. `queuedWithdrawalsBuffer[ETH]` remains `0` (default).
2. Operator completes an EigenLayer withdrawal; vault now holds `X ETH`.
3. User A calls `initiateWithdrawal(ETH, rsETHAmount)`. `assetsCommitted[ETH] += X`. User A's rsETH is held in the withdrawal manager.
4. User B (unprivileged) calls `instantWithdrawal(ETH, rsETHAmount2)`. `getAssetsAvailableForInstantWithdrawal(ETH)` returns `X - 0 = X`. The check passes. `unstakingVault.redeem(ETH, X)` drains the vault to zero.
5. Operator calls `unlockQueue(ETH, ...)`. `totalAvailableAssets = unstakingVault.balanceOf(ETH) = 0`. The `if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero()` check at line 297 causes a revert, or if assets are nonzero but insufficient, `_unlockWithdrawalRequests` breaks immediately at line 800.
6. User A's withdrawal is frozen. Their rsETH remains locked in the contract until the operator manually replenishes the vault from EigenLayer.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L170-173)
```text
        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L231-235)
```text
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L800-800)
```text
            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request
```

**File:** contracts/LRTWithdrawalManager.sol (L847-850)
```text
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
```

**File:** contracts/LRTUnstakingVault.sol (L199-208)
```text
    function setQueuedWithdrawalsBuffer(
        address asset,
        uint256 buffer
    )
        external
        onlyLRTOperator
        onlySupportedAsset(asset)
    {
        queuedWithdrawalsBuffer[asset] = buffer;
        emit QueuedWithdrawalsBufferUpdated(asset, buffer);
```

**File:** contracts/LRTUnstakingVault.sol (L235-238)
```text
        uint256 vaultBalance = balanceOf(asset);
        uint256 reservedBuffer = queuedWithdrawalsBuffer[asset];
        availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
    }
```
