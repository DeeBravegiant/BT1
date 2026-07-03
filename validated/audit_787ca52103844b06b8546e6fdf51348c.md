### Title
Admin Removal of Supported Asset Permanently Freezes Pending Withdrawal Requests - (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager.unlockQueue` enforces `onlySupportedAsset(asset)` at call time, but `initiateWithdrawal` already commits user rsETH to a withdrawal request for a specific asset. If an admin later removes that asset via `LRTConfig.removeSupportedAsset`, `unlockQueue` permanently reverts for that asset, the withdrawal queue can never advance, and every pending `completeWithdrawal` call reverts with `WithdrawalLocked`. Users' rsETH is irreversibly stuck in the contract with no cancellation path.

---

### Finding Description

The two-phase withdrawal flow is:

1. **`initiateWithdrawal`** — user transfers rsETH to `LRTWithdrawalManager` and a `WithdrawalRequest` is stored. The function enforces `onlySupportedAsset(asset)` and `onlySupportedStrategy(asset)` at initiation time. [1](#0-0) 

2. **`unlockQueue`** (operator-called) — advances `nextLockedNonce[asset]` so requests become claimable. It also enforces `onlySupportedAsset(asset)`. [2](#0-1) 

3. **`completeWithdrawal`** — user claims funds. It has **no** `onlySupportedAsset` guard, but it requires `usersFirstWithdrawalRequestNonce < nextLockedNonce[asset]`; if the queue was never unlocked, this always reverts with `WithdrawalLocked`. [3](#0-2) 

An admin can call `LRTConfig.removeSupportedAsset`, which deletes `isSupportedAsset[asset]` and `assetStrategy[asset]`: [4](#0-3) 

After removal, every call to `unlockQueue` for that asset reverts because `onlySupportedAsset` in `LRTConfigRoleChecker` reads `lrtConfig.isSupportedAsset(asset)`: [5](#0-4) 

The `removeSupportedAsset` function contains a partial guard:

```solidity
if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
    revert CannotRemoveAssetWithDeposits(asset);
}
``` [6](#0-5) 

However, this guard is insufficient because:
- `maxNegligibleAmount` is itself admin-configurable via `setMaxNegligibleAmount` with no lower bound.
- `getTotalAssetDeposits` measures assets in the deposit pool / EigenLayer, not the rsETH already committed inside `LRTWithdrawalManager`. A scenario where all underlying assets have been moved to EigenLayer (so the deposit pool reports zero) while rsETH is locked in the withdrawal manager is realistic during normal operations. [7](#0-6) 

There is no `cancelWithdrawal` function in `LRTWithdrawalManager`, so users have no recovery path.

---

### Impact Explanation

**Medium — Temporary (potentially permanent) freezing of user rsETH funds.**

All rsETH transferred during `initiateWithdrawal` for the removed asset is locked inside `LRTWithdrawalManager` with no mechanism to unlock or return it. The `unlockQueue` call permanently reverts, `completeWithdrawal` permanently reverts with `WithdrawalLocked`, and no cancellation function exists. Recovery requires the admin to re-add the asset (via `TIME_LOCK_ROLE`) and re-configure its strategy — a non-trivial multi-step governance action that is not guaranteed to occur.

---

### Likelihood Explanation

**Low-Medium.** The scenario requires an admin to remove a supported asset (a legitimate governance action, e.g., deprecating a poorly-performing LST) while withdrawal requests for that asset are still pending in the queue. The partial guard in `removeSupportedAsset` reduces but does not eliminate the risk, since `maxNegligibleAmount` is admin-settable and the guard does not account for rsETH already committed in the withdrawal manager. This is an unintended consequence of a routine admin action, matching the severity classification of the reference finding.

---

### Recommendation

1. **Remove `onlySupportedAsset` from `unlockQueue`**, or replace it with a weaker check that allows processing of pre-existing requests even after an asset is delisted.
2. **Add a `cancelWithdrawal` function** that returns rsETH to the user if their request has not yet been unlocked, so users are not permanently harmed by asset removal.
3. **Strengthen the guard in `removeSupportedAsset`** to also check `LRTWithdrawalManager.assetsCommitted[asset] > 0` and revert if pending withdrawal commitments exist for the asset being removed.

---

### Proof of Concept

1. Asset `stETH` is supported; user calls `initiateWithdrawal(stETH, 10 ether)`. rsETH is transferred to `LRTWithdrawalManager`; `withdrawalRequests[requestId]` is stored; `nextUnusedNonce[stETH]` advances.
2. Admin calls `LRTConfig.removeSupportedAsset(stETH, 0)`. The guard passes because all stETH has been moved to EigenLayer (`getTotalAssetDeposits` returns 0). `isSupportedAsset[stETH]` is deleted.
3. Operator calls `unlockQueue(stETH, ...)`. Reverts: `AssetNotSupported` (from `onlySupportedAsset`). [8](#0-7) 
4. `nextLockedNonce[stETH]` never advances.
5. User calls `completeWithdrawal(stETH, ...)`. `_processWithdrawalCompletion` checks `usersFirstWithdrawalRequestNonce >= nextLockedNonce[stETH]` → reverts `WithdrawalLocked`. [9](#0-8) 
6. User's rsETH is permanently frozen in `LRTWithdrawalManager` with no recovery path.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L150-178)
```text
    function initiateWithdrawal(
        address asset,
        uint256 rsETHUnstaked,
        string calldata referralId
    )
        external
        override
        nonReentrant
        whenNotPaused
        onlySupportedAsset(asset)
        onlySupportedStrategy(asset)
    {
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L268-281)
```text
    function unlockQueue(
        address asset,
        uint256 firstExcludedIndex,
        uint256 minimumAssetPrice,
        uint256 minimumRsEthPrice,
        uint256 maximumAssetPrice,
        uint256 maximumRsEthPrice
    )
        external
        nonReentrant
        onlySupportedAsset(asset)
        whenNotPaused
        onlyAssetTransferOrOperatorRole
        returns (uint256 rsETHBurned, uint256 assetAmountUnlocked)
```

**File:** contracts/LRTWithdrawalManager.sol (L699-715)
```text
    function _processWithdrawalCompletion(address asset, address user, string calldata referralId) internal {
        if (userAssociatedNonces[asset][user].empty()) {
            revert NoWithdrawalRequests(user, asset);
        }

        // Retrieve and remove the oldest withdrawal request for the user.
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();

        bytes32 requestId = getRequestId(asset, usersFirstWithdrawalRequestNonce);
        WithdrawalRequest memory request = withdrawalRequests[requestId];

        delete withdrawalRequests[requestId];

        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
```

**File:** contracts/LRTConfig.sol (L64-94)
```text
    /// @dev Removes a supported asset
    /// @param asset The asset address
    function removeSupportedAsset(
        address asset,
        uint256 tokenIndex
    )
        external
        onlySupportedAsset(asset)
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        UtilLib.checkNonZeroAddress(asset);

        if (supportedAssetList[tokenIndex] != asset) {
            revert TokenNotFoundError();
        }

        address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
            revert CannotRemoveAssetWithDeposits(asset);
        }

        delete isSupportedAsset[asset];
        delete assetStrategy[asset];
        depositLimitByAsset[asset] = 0;

        supportedAssetList[tokenIndex] = supportedAssetList[supportedAssetList.length - 1];
        supportedAssetList.pop();

        emit RemovedSupportedAsset(asset);
    }
```

**File:** contracts/LRTConfig.sol (L253-259)
```text
    /// @notice maximum amount that can be ignored
    /// @dev only callable by LRT admin
    /// @param maxNegligibleAmount_ Maximum amount that can be ignored
    function setMaxNegligibleAmount(uint256 maxNegligibleAmount_) external onlyRole(DEFAULT_ADMIN_ROLE) {
        maxNegligibleAmount = maxNegligibleAmount_;
        emit MaxNegligibleAmountUpdated(maxNegligibleAmount_);
    }
```

**File:** contracts/utils/LRTConfigRoleChecker.sol (L65-70)
```text
    modifier onlySupportedAsset(address asset) {
        if (!lrtConfig.isSupportedAsset(asset)) {
            revert ILRTConfig.AssetNotSupported();
        }
        _;
    }
```
