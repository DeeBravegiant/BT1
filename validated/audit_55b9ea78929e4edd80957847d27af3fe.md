### Title
`instantWithdrawal` Does Not Account for `assetsCommitted` in Vault Availability Check — (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

The `instantWithdrawal` function checks available vault assets via `LRTUnstakingVault.getAssetsAvailableForInstantWithdrawal`, which only subtracts a static, operator-set `queuedWithdrawalsBuffer` from the vault balance. It never subtracts `assetsCommitted[asset]`, the live accounting variable that tracks all assets already committed to pending queued withdrawal requests. Because `queuedWithdrawalsBuffer` defaults to `0` and is never automatically updated when `assetsCommitted` grows, an unprivileged caller can drain the unstaking vault through instant withdrawals even when the entire vault balance is already committed to queued withdrawals, freezing those queued users' funds.

---

### Finding Description

**Step 1 — Queued withdrawal commits assets.**

When a user calls `initiateWithdrawal`, the protocol records the commitment:

```solidity
// LRTWithdrawalManager.sol:168-173
uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();
// preventing over-withdrawal.
assetsCommitted[asset] += expectedAssetAmount;
```

`assetsCommitted[asset]` is the canonical tracker of how much of the vault's assets are already spoken for. [1](#0-0) 

**Step 2 — `getAvailableAssetAmount` correctly uses `assetsCommitted`.**

```solidity
// LRTWithdrawalManager.sol:599-603
function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
    ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
    uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
    availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
}
```

The queued-withdrawal path is protected. [2](#0-1) 

**Step 3 — `instantWithdrawal` uses a completely separate, static check.**

```solidity
// LRTWithdrawalManager.sol:228-233
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
    revert CantInstantWithdrawMoreThanAvailable();
}
``` [3](#0-2) 

**Step 4 — `getAssetsAvailableForInstantWithdrawal` ignores `assetsCommitted`.**

```solidity
// LRTUnstakingVault.sol:229-238
function getAssetsAvailableForInstantWithdrawal(address asset) external view returns (uint256 availableAmount) {
    uint256 vaultBalance = balanceOf(asset);
    uint256 reservedBuffer = queuedWithdrawalsBuffer[asset];
    availableAmount = reservedBuffer >= vaultBalance ? 0 : vaultBalance - reservedBuffer;
}
```

`queuedWithdrawalsBuffer[asset]` is a mapping whose default value is `0`. It is only updated by an explicit operator call to `setQueuedWithdrawalsBuffer`. It is **never** automatically synchronized with `assetsCommitted[asset]`. [4](#0-3) 

**Step 5 — `unlockQueue` can only fulfill queued withdrawals from the actual vault balance.**

```solidity
// LRTWithdrawalManager.sol:846-850
return UnlockParams({
    rsETHPrice: lrtOracle.rsETHPrice(),
    assetPrice: lrtOracle.getAssetPrice(asset),
    totalAvailableAssets: unstakingVault.balanceOf(asset)   // raw vault balance
});
```

If the vault has been drained by instant withdrawals, `totalAvailableAssets = 0` and no queued withdrawal can ever be unlocked. [5](#0-4) 

---

### Impact Explanation

Any user whose queued withdrawal was accepted (their rsETH already transferred into `LRTWithdrawalManager`) cannot complete that withdrawal until the operator manually replenishes the unstaking vault. Their rsETH is locked inside the contract with no self-service exit. This is **temporary freezing of funds** (Medium).

---

### Likelihood Explanation

- Instant withdrawal is disabled by default (`isInstantWithdrawalEnabled[asset] = false`), so the operator must enable it. This is a realistic operational step.
- `queuedWithdrawalsBuffer` defaults to `0`. The protocol provides no enforcement or reminder to set it when enabling instant withdrawal. An operator enabling instant withdrawal without setting the buffer is a realistic omission.
- Once those two conditions hold, any unprivileged caller can drain the vault in a single transaction, with no capital at risk (they receive fair-value assets in return for their rsETH).

---

### Recommendation

In `instantWithdrawal`, subtract `assetsCommitted[asset]` from the vault balance before comparing:

```solidity
uint256 vaultBalance = unstakingVault.balanceOf(asset);
uint256 committed    = assetsCommitted[asset];
uint256 trueAvailable = vaultBalance > committed ? vaultBalance - committed : 0;
if (assetAmountUnlocked > trueAvailable) revert CantInstantWithdrawMoreThanAvailable();
```

Alternatively, `getAssetsAvailableForInstantWithdrawal` should accept and subtract the live `assetsCommitted` value rather than relying on a manually maintained static buffer.

---

### Proof of Concept

1. Operator calls `setInstantWithdrawalEnabled(ETH, true)`. `queuedWithdrawalsBuffer[ETH]` remains `0` (default).
2. Unstaking vault holds **100 ETH**.
3. User A calls `initiateWithdrawal(ETH, rsETHAmount_A)` → `assetsCommitted[ETH] = 80 ETH`. User A's rsETH is now locked in `LRTWithdrawalManager`.
4. Attacker calls `instantWithdrawal(ETH, rsETHAmount_B)` where `rsETHAmount_B` corresponds to **100 ETH** at the current price.
   - Check: `100 ETH <= getAssetsAvailableForInstantWithdrawal(ETH) = 100 - 0 = 100 ETH` → **passes**.
   - Vault is drained to **0 ETH**. Attacker receives 100 ETH.
5. Operator calls `unlockQueue(ETH, ...)` to fulfill User A's withdrawal.
   - `totalAvailableAssets = unstakingVault.balanceOf(ETH) = 0`.
   - `_unlockWithdrawalRequests` exits immediately: `if (availableAssetAmount < payoutAmount) break`.
   - User A's withdrawal **cannot be unlocked**. Their rsETH remains locked in `LRTWithdrawalManager` indefinitely until the operator manually replenishes the vault. [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L162-173)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

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

**File:** contracts/LRTWithdrawalManager.sol (L599-603)
```text
    function getAvailableAssetAmount(address asset) public view override returns (uint256 availableAssetAmount) {
        ILRTDepositPool lrtDepositPool = ILRTDepositPool(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        uint256 totalAssets = lrtDepositPool.getTotalAssetDeposits(asset);
        availableAssetAmount = totalAssets > assetsCommitted[asset] ? totalAssets - assetsCommitted[asset] : 0;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L797-802)
```text
            // Calculate the amount user will receive
            uint256 payoutAmount = _calculatePayoutAmount(request, rsETHPrice, assetPrice);

            if (availableAssetAmount < payoutAmount) break; // Exit if not enough assets to cover this request

            assetsCommitted[asset] -= request.expectedAssetAmount;
```

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
```

**File:** contracts/LRTUnstakingVault.sol (L196-238)
```text
    /// @notice Set the reserved buffer for queued withdrawals for an asset.
    /// @param asset The asset address.
    /// @param buffer The reserved amount for queued withdrawals.
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

    /*//////////////////////////////////////////////////////////////
                            view functions
    //////////////////////////////////////////////////////////////*/

    /// @notice Returns the the vaults balance of the asset.
    /// @param asset The asset address.
    /// @return The balance of the asset.
    function balanceOf(address asset) public view returns (uint256) {
        if (asset == LRTConstants.ETH_TOKEN) {
            return address(this).balance;
        } else {
            return IERC20(asset).balanceOf(address(this));
        }
    }

    /// @notice Returns the amount of the asset available for instant withdrawal.
    /// @param asset The asset address.
    /// @return availableAmount The amount of the asset available for instant withdrawal.
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
