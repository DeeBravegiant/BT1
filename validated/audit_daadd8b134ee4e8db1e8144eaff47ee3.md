Audit Report

## Title
`instantWithdrawal` Uses Manually-Set `queuedWithdrawalsBuffer` Instead of `assetsCommitted`, Enabling Vault Drain and Freezing Queued Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`instantWithdrawal` gates vault access via `LRTUnstakingVault.getAssetsAvailableForInstantWithdrawal`, which subtracts only the operator-set `queuedWithdrawalsBuffer` (defaults to zero) from the vault balance. It never consults `assetsCommitted[asset]`, the on-chain variable that is automatically incremented when users call `initiateWithdrawal`. Any rsETH holder can drain the vault of assets already promised to queued withdrawal users, causing `unlockQueue` to revert and leaving queued users' rsETH locked in `LRTWithdrawalManager` with no cancel path.

## Finding Description

**Step 1 — Queued withdrawal commits assets:**
When a user calls `initiateWithdrawal`, their rsETH is transferred to `LRTWithdrawalManager` and `assetsCommitted[asset]` is incremented by `expectedAssetAmount`. [1](#0-0) 

**Step 2 — `instantWithdrawal` ignores `assetsCommitted`:**
`instantWithdrawal` calls `unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)`, which computes `vaultBalance - queuedWithdrawalsBuffer[asset]`. [2](#0-1) 

**Step 3 — `queuedWithdrawalsBuffer` defaults to zero:**
`queuedWithdrawalsBuffer` is a Solidity mapping that defaults to zero and is only updated by an explicit operator call to `setQueuedWithdrawalsBuffer`. When unset, `getAssetsAvailableForInstantWithdrawal` returns the full vault balance, regardless of how much is committed to queued withdrawals. [3](#0-2) [4](#0-3) 

**Step 4 — Vault drained; `unlockQueue` reverts:**
`unlockQueue` reads `unstakingVault.balanceOf(asset)` as `totalAvailableAssets` via `_createUnlockParams` and immediately reverts if it is zero. [5](#0-4) [6](#0-5) 

**Step 5 — No cancel path for queued users:**
`_processWithdrawalCompletion` requires the request nonce to be below `nextLockedNonce[asset]`, which is only advanced by a successful `unlockQueue`. Since `unlockQueue` reverts, queued users cannot complete their withdrawals. [7](#0-6) 

The two accounting systems — `assetsCommitted` (auto-updated) and `queuedWithdrawalsBuffer` (manually set, defaults to 0) — are never automatically synchronized, so the gap can persist indefinitely.

## Impact Explanation

**Medium. Temporary freezing of funds.** Queued withdrawal users' rsETH is locked in `LRTWithdrawalManager` until the vault is replenished (e.g., via EigenLayer's `completeUnstaking`). There is no cancel mechanism for queued withdrawals, so users cannot recover their rsETH during the freeze. An attacker can repeatedly drain the vault as soon as it is replenished if instant withdrawals remain enabled and the buffer is not corrected.

## Likelihood Explanation

The precondition is that instant withdrawals are enabled for the asset (`isInstantWithdrawalEnabled[asset] == true`), which is a normal operational state set by the manager. Once enabled, any rsETH holder can call `instantWithdrawal`. The attack window opens the moment a user calls `initiateWithdrawal` and the operator has not set `queuedWithdrawalsBuffer` to at least `assetsCommitted[asset]`. Since the buffer defaults to zero and requires explicit operator action to update, this window exists by default and can persist indefinitely. The attack is low-barrier, permissionless (for any rsETH holder), and repeatable.

## Recommendation

Replace the `queuedWithdrawalsBuffer`-based check in `getAssetsAvailableForInstantWithdrawal` (or in `instantWithdrawal` itself) with a check against `assetsCommitted[asset]` from `LRTWithdrawalManager`. Concretely, the available amount for instant withdrawal should be:

```
availableForInstant = vaultBalance > assetsCommitted[asset]
    ? vaultBalance - assetsCommitted[asset]
    : 0;
```

This ensures the vault always retains enough assets to cover all committed queued withdrawals. The `queuedWithdrawalsBuffer` mechanism can be retained as an additional operator-controlled reserve on top of this floor, but it must not be the sole protection.

## Proof of Concept

**Minimal Foundry call sequence:**

1. Deploy/fork with `isInstantWithdrawalEnabled[asset] = true` and `queuedWithdrawalsBuffer[asset] = 0` (default).
2. Fund `LRTUnstakingVault` with `N` units of `asset` (simulating completed EigenLayer unstaking).
3. `userA` calls `initiateWithdrawal(asset, rsETHAmount)` → `assetsCommitted[asset]` becomes `N`, userA's rsETH locked.
4. `attacker` (any rsETH holder) calls `instantWithdrawal(asset, rsETHAmount2)` where `rsETHAmount2` corresponds to `N` asset units → vault drained to 0.
5. Operator calls `unlockQueue(asset, ...)` → reverts with `AmountMustBeGreaterThanZero` because `unstakingVault.balanceOf(asset) == 0`.
6. `userA` calls `completeWithdrawal(asset)` → reverts with `WithdrawalLocked` because `nextLockedNonce` was never advanced.
7. `userA`'s rsETH remains permanently locked until vault is externally replenished.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-173)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L228-235)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L297-297)
```text
        if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();
```

**File:** contracts/LRTWithdrawalManager.sol (L705-707)
```text
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
```

**File:** contracts/LRTUnstakingVault.sol (L199-209)
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
    }
```

**File:** contracts/LRTUnstakingVault.sol (L229-238)
```text
    function getAssetsAvailableForInstantWithdrawal(address asset)
        external
        view
        onlySupportedAsset(asset)
        returns (uint256 availableAmount)
    {
        uint256 vaultBalance = balanceOf(asset);
        uint256 reservedBuffer = queuedWithdrawalsBuffer[asset];
        availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
    }
```
