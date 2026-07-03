Audit Report

## Title
`instantWithdrawal()` Liquidity Check Does Not Account for Queued Withdrawal Commitments, Enabling Temporary Freeze of Queued Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

## Summary

`LRTWithdrawalManager.instantWithdrawal()` validates available liquidity via `LRTUnstakingVault.getAssetsAvailableForInstantWithdrawal()`, which subtracts a static, operator-set `queuedWithdrawalsBuffer` from the vault balance. This buffer is never automatically updated when `assetsCommitted[asset]` grows via `initiateWithdrawal()`. As a result, any user can drain vault funds already committed to pending queued withdrawals, causing `unlockQueue()` to revert and temporarily freezing queued withdrawal users' rsETH.

## Finding Description

The protocol maintains two separate accounting values for vault liquidity:

**`assetsCommitted[asset]`** — dynamically incremented in `LRTWithdrawalManager.initiateWithdrawal()` each time a user queues a withdrawal:
```
assetsCommitted[asset] += expectedAssetAmount;  // LRTWithdrawalManager.sol L173
```

**`queuedWithdrawalsBuffer[asset]`** — a static value set exclusively by an operator via `LRTUnstakingVault.setQueuedWithdrawalsBuffer()`, defaulting to 0 and never automatically updated:
```
queuedWithdrawalsBuffer[asset] = buffer;  // LRTUnstakingVault.sol L207
```

`getAssetsAvailableForInstantWithdrawal()` uses only the static buffer:
```solidity
// LRTUnstakingVault.sol L235-237
uint256 vaultBalance = balanceOf(asset);
uint256 reservedBuffer = queuedWithdrawalsBuffer[asset];
availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
```

`instantWithdrawal()` relies solely on this check before redeeming from the vault:
```solidity
// LRTWithdrawalManager.sol L231-233
if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
    revert CantInstantWithdrawMoreThanAvailable();
}
unstakingVault.redeem(asset, assetAmountUnlocked);
```

When `queuedWithdrawalsBuffer` is 0 (the default), `getAssetsAvailableForInstantWithdrawal` returns the full vault balance, regardless of how much is committed to queued withdrawals. An instant withdrawal user can therefore redeem the entire vault balance.

`unlockQueue()` then reads the raw vault balance as available assets:
```solidity
// LRTWithdrawalManager.sol L849
totalAvailableAssets: unstakingVault.balanceOf(asset)
```

If the vault has been drained to 0, `unlockQueue()` hits the guard at line 297:
```solidity
if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();
```

This reverts the entire call, making it impossible to unlock any queued withdrawal until the vault is manually refilled by operators.

**Exploit flow:**
1. Vault holds 100 ETH; `queuedWithdrawalsBuffer[ETH]` = 0 (default); instant withdrawal is enabled.
2. Users call `initiateWithdrawal()` totaling 100 ETH → `assetsCommitted[ETH]` = 100 ETH; their rsETH is locked in `LRTWithdrawalManager`.
3. Any user calls `instantWithdrawal()` for 100 ETH. Check passes: `getAssetsAvailableForInstantWithdrawal` = 100 − 0 = 100 ETH. Vault drained to 0.
4. Operator calls `unlockQueue()`. `_createUnlockParams` reads `unstakingVault.balanceOf(asset)` = 0. `unlockQueue` reverts at line 297.
5. Queued withdrawal users' rsETH remains locked in `LRTWithdrawalManager` with no path to completion until operators manually refill the vault.

## Impact Explanation

**Medium — Temporary freezing of funds.**

Queued withdrawal users have already transferred their rsETH into `LRTWithdrawalManager` (line 166) and cannot retrieve it until `unlockQueue()` succeeds. With the vault drained, `unlockQueue()` reverts unconditionally. Funds are not permanently lost — the protocol still holds equivalent assets in EigenLayer/NDCs — but users are frozen until operators manually move assets back into the vault. This constitutes a temporary freeze of user funds, matching the allowed impact scope.

## Likelihood Explanation

**High.** `queuedWithdrawalsBuffer` defaults to 0 for all assets. The only precondition is that instant withdrawal is enabled for the asset (`isInstantWithdrawalEnabled[asset]`), which is a manager-controlled toggle intended for normal operation. Once enabled, any rsETH holder can call `instantWithdrawal()` at any time without any special role or privilege. The condition is reachable whenever the vault holds funds committed to queued withdrawals and instant withdrawal is active — a normal operational state.

## Recommendation

Replace the static `queuedWithdrawalsBuffer` check in `LRTUnstakingVault.getAssetsAvailableForInstantWithdrawal()` with a dynamic check that reads the actual `assetsCommitted[asset]` from `LRTWithdrawalManager`. Specifically:

```solidity
function getAssetsAvailableForInstantWithdrawal(address asset) external view returns (uint256) {
    uint256 vaultBalance = balanceOf(asset);
    ILRTWithdrawalManager wm = ILRTWithdrawalManager(lrtConfig.withdrawManager());
    uint256 committed = wm.assetsCommitted(asset);
    return committed >= vaultBalance ? 0 : vaultBalance - committed;
}
```

This ensures that funds already committed to queued withdrawals are always excluded from instant withdrawal availability, regardless of whether an operator has manually updated the buffer.

## Proof of Concept

**Root cause — static buffer vs. dynamic commitments:**

`getAssetsAvailableForInstantWithdrawal` uses `queuedWithdrawalsBuffer` (static): [1](#0-0) 

`queuedWithdrawalsBuffer` is only updated by explicit operator call, never by `initiateWithdrawal`: [2](#0-1) 

`instantWithdrawal` relies solely on this stale check before redeeming: [3](#0-2) 

`initiateWithdrawal` increments `assetsCommitted` dynamically but does not update `queuedWithdrawalsBuffer`: [4](#0-3) 

`unlockQueue` uses raw vault balance as available assets — zero if drained: [5](#0-4) 

`unlockQueue` reverts immediately when vault balance is 0: [6](#0-5) 

**Foundry test plan:**
1. Deploy protocol with ETH as a supported asset; enable instant withdrawal.
2. Fund `LRTUnstakingVault` with 100 ETH; leave `queuedWithdrawalsBuffer[ETH]` = 0.
3. Have two users call `initiateWithdrawal()` for 50 ETH each → assert `assetsCommitted[ETH]` = 100 ETH.
4. Have a third user call `instantWithdrawal()` for 100 ETH → assert vault balance = 0.
5. Call `unlockQueue()` → assert it reverts with `AmountMustBeGreaterThanZero`.
6. Assert queued withdrawal users' rsETH is still locked in `LRTWithdrawalManager`.

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L168-175)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L230-235)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L846-851)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
    }
```
