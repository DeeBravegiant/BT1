Audit Report

## Title
`instantWithdrawal()` Liquidity Check Does Not Account for Queued Withdrawal Commitments, Enabling Vault Drain That Freezes Queued Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`LRTWithdrawalManager.instantWithdrawal()` validates available liquidity against `LRTUnstakingVault.getAssetsAvailableForInstantWithdrawal()`, which subtracts only the static, operator-set `queuedWithdrawalsBuffer` from the vault balance. Because `queuedWithdrawalsBuffer` defaults to zero and is never automatically updated when `assetsCommitted` grows via `initiateWithdrawal()`, any user can drain vault funds that are already committed to pending queued withdrawals. Once drained, `unlockQueue()` reverts with `AmountMustBeGreaterThanZero`, leaving queued withdrawal users' rsETH locked in `LRTWithdrawalManager` until operators manually refill the vault.

## Finding Description

**Two withdrawal paths draw from the same `LRTUnstakingVault`:**

**Path 1 – Queued withdrawal (`initiateWithdrawal` → `unlockQueue`):**
`initiateWithdrawal` increments `assetsCommitted[asset]` by the committed amount at [1](#0-0)  and transfers the user's rsETH into the contract at [2](#0-1) . Later, `unlockQueue` calls `_createUnlockParams`, which reads `totalAvailableAssets` as the raw vault balance: [3](#0-2) 

**Path 2 – Instant withdrawal (`instantWithdrawal`):**
The availability check uses `getAssetsAvailableForInstantWithdrawal`, which subtracts only the static `queuedWithdrawalsBuffer`: [4](#0-3) 

`queuedWithdrawalsBuffer` is set exclusively by an explicit operator call and is never automatically updated when `assetsCommitted` grows: [5](#0-4) 

**The mismatch:** `assetsCommitted[asset]` grows with every `initiateWithdrawal` call, but `queuedWithdrawalsBuffer[asset]` remains at its last manually set value (zero by default). `instantWithdrawal` checks only the static buffer: [6](#0-5) 

**Exploit flow:**
1. Vault holds 100 ETH; `queuedWithdrawalsBuffer[ETH]` = 0 (default).
2. Users call `initiateWithdrawal` totaling 100 ETH → `assetsCommitted[ETH]` = 100 ETH; their rsETH is locked in `LRTWithdrawalManager`.
3. Any user calls `instantWithdrawal` for 100 ETH. Check: `getAssetsAvailableForInstantWithdrawal` = 100 − 0 = 100 ETH. Passes. Vault drained to 0.
4. Operator calls `unlockQueue`. `_createUnlockParams` reads `unstakingVault.balanceOf(asset)` = 0 → `params.totalAvailableAssets` = 0. The guard at line 297 reverts with `AmountMustBeGreaterThanZero`. [7](#0-6) 
5. Queued withdrawal users' rsETH remains locked in `LRTWithdrawalManager` with no path to completion until operators manually move assets back into the vault.

**Existing guards are insufficient:** The `queuedWithdrawalsBuffer` mechanism is the only protection for queued withdrawal funds in the instant withdrawal path, but it is a manually maintained value that becomes stale the moment any `initiateWithdrawal` is processed. There is no on-chain enforcement linking the two accounting systems.

## Impact Explanation

**Medium – Temporary freezing of funds.** Queued withdrawal users have already transferred their rsETH into `LRTWithdrawalManager` and cannot retrieve it until `unlockQueue` succeeds. With the vault drained, `unlockQueue` reverts, blocking all queued withdrawal completions. Funds are not permanently lost — the protocol still holds equivalent assets in EigenLayer/NDCs — but users are frozen until operators manually move assets back into the vault. This matches the allowed impact: **Medium. Temporary freezing of funds.**

## Likelihood Explanation

**High.** `queuedWithdrawalsBuffer` defaults to 0 for all assets. Any user holding rsETH can call `instantWithdrawal` at any time when instant withdrawal is enabled for an asset. No special role or privilege is required. The condition is reachable in normal protocol operation whenever the vault holds funds that are simultaneously committed to queued withdrawals — a routine state during the queued withdrawal lifecycle.

## Recommendation

Replace the static `queuedWithdrawalsBuffer` check in `LRTUnstakingVault.getAssetsAvailableForInstantWithdrawal` with a dynamic check that reads the actual `assetsCommitted[asset]` from `LRTWithdrawalManager`. Specifically, `getAssetsAvailableForInstantWithdrawal` should subtract `ILRTWithdrawalManager(withdrawalManager).assetsCommitted(asset)` from the vault balance instead of (or in addition to) the static buffer. This ensures that funds already committed to queued withdrawals are always excluded from the instant withdrawal availability calculation, regardless of whether an operator has manually updated the buffer.

## Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.0;

// Foundry fork test outline:
// 1. Fork mainnet with vault holding 100 ETH, queuedWithdrawalsBuffer[ETH] = 0.
// 2. Two users call initiateWithdrawal(ETH, 50e18) each.
//    → assetsCommitted[ETH] = 100 ETH; both users' rsETH locked in WithdrawalManager.
// 3. Attacker (any rsETH holder) calls instantWithdrawal(ETH, rsETHFor100ETH).
//    → getAssetsAvailableForInstantWithdrawal returns 100 ETH (buffer = 0).
//    → unstakingVault.redeem drains vault to 0.
// 4. Operator calls unlockQueue(ETH, ...).
//    → _createUnlockParams: totalAvailableAssets = unstakingVault.balanceOf(ETH) = 0.
//    → Reverts: AmountMustBeGreaterThanZero.
// 5. Assert: queued withdrawal users cannot complete withdrawals.
//    Their rsETH remains locked in LRTWithdrawalManager.
```

The root cause is confirmed by the static buffer read at [4](#0-3)  versus the dynamic commitment increment at [1](#0-0) , with `unlockQueue` reading only the raw vault balance at [8](#0-7)  and reverting when it is zero at [7](#0-6) .

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L172-173)
```text
        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L230-233)
```text
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }
```

**File:** contracts/LRTWithdrawalManager.sol (L297-297)
```text
        if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();
```

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
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

**File:** contracts/LRTUnstakingVault.sol (L235-237)
```text
        uint256 vaultBalance = balanceOf(asset);
        uint256 reservedBuffer = queuedWithdrawalsBuffer[asset];
        availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
```
