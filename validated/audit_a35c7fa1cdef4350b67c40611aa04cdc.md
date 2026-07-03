### Title
rsETH Permanently Frozen in `LRTWithdrawalManager` When Asset Is Removed From Supported List - (File: contracts/LRTWithdrawalManager.sol)

### Summary
When a user initiates a withdrawal via `LRTWithdrawalManager.initiateWithdrawal()`, their rsETH is transferred into the contract and held pending an operator call to `unlockQueue()`. If the underlying asset is subsequently removed from the supported list via `LRTConfig.removeSupportedAsset()`, the `unlockQueue()` function becomes permanently uncallable for that asset due to its `onlySupportedAsset(asset)` guard. With no `cancelWithdrawal` function and no recovery path, the rsETH is permanently frozen in the contract.

### Finding Description

The withdrawal lifecycle in `LRTWithdrawalManager` is a two-phase process:

**Phase 1 – `initiateWithdrawal`**: The user's rsETH is pulled into the contract and a `WithdrawalRequest` is stored. The request is in a "locked" state until an operator advances `nextLockedNonce[asset]`. [1](#0-0) 

**Phase 2 – `unlockQueue`**: An operator calls this to advance `nextLockedNonce[asset]`, burn the rsETH, and pull the underlying asset from the unstaking vault. It carries a hard `onlySupportedAsset(asset)` guard. [2](#0-1) 

**Phase 3 – `completeWithdrawal`**: The user calls this to receive their asset. It requires `usersFirstWithdrawalRequestNonce < nextLockedNonce[asset]`; if `unlockQueue` was never called, this check always reverts with `WithdrawalLocked`. [3](#0-2) 

`LRTConfig.removeSupportedAsset()` deletes `isSupportedAsset[asset]` and `assetStrategy[asset]`, which causes every function guarded by `onlySupportedAsset` to revert. [4](#0-3) 

After removal, the following functions all revert for the removed asset:
- `unlockQueue` — `onlySupportedAsset` guard
- `sweepRemainingAssets` — also has `onlySupportedAsset` guard [5](#0-4) 

There is no `cancelWithdrawal` function, no `recoverTokens` function (the contract does not inherit `Recoverable`), and no admin escape hatch for the rsETH held in the contract. The rsETH is permanently unrecoverable.

### Impact Explanation

Any rsETH transferred to `LRTWithdrawalManager` via `initiateWithdrawal` for an asset that is subsequently removed from the supported list is permanently frozen. The user loses their rsETH (their liquid restaking position) with no recourse. This is a **Critical – Permanent freezing of funds**.

### Likelihood Explanation

`removeSupportedAsset` has a partial guard: [6](#0-5) 

This check compares `getTotalAssetDeposits(asset)` against `maxNegligibleAmount`. However, `getTotalAssetDeposits` does not account for rsETH already locked in `LRTWithdrawalManager` (it counts assets in the deposit pool, NDCs, EigenLayer, converter, and unstaking vault — not the withdrawal manager's rsETH balance). The freeze can occur when:

1. Underlying assets are slashed to near-zero in EigenLayer, making `getTotalAssetDeposits ≈ 0` while rsETH is still locked in the withdrawal manager for pending requests.
2. `maxNegligibleAmount` is set to a permissive value by the admin, allowing removal despite residual deposits.

The scenario requires an admin action (asset removal) combined with an adverse protocol state (slashing or negligible deposits), making likelihood **Low**, but the impact is **Critical**.

### Recommendation

1. **Short term**: Add a `cancelWithdrawal(address asset, uint256 nonce)` function that allows users to reclaim their rsETH when the asset is no longer supported or the request cannot be unlocked. Alternatively, remove the `onlySupportedAsset` guard from `unlockQueue` so that already-queued requests for deprecated assets can still be processed.
2. **Long term**: Before allowing `removeSupportedAsset` to succeed, verify that `assetsCommitted[asset] == 0` in `LRTWithdrawalManager`, ensuring no pending withdrawal requests exist for the asset being removed. Document all withdrawal request states and the transitions between them.

### Proof of Concept

```
1. Alice calls initiateWithdrawal(stETH, 10e18, "") 
   → 10e18 rsETH transferred from Alice to LRTWithdrawalManager
   → withdrawalRequests[requestId] = {rsETHUnstaked: 10e18, ...}
   → nextLockedNonce[stETH] = 0 (request is locked)

2. stETH is slashed in EigenLayer → getTotalAssetDeposits(stETH) ≈ 0

3. Admin calls LRTConfig.removeSupportedAsset(stETH, idx)
   → getTotalAssetDeposits(stETH) <= maxNegligibleAmount → passes
   → isSupportedAsset[stETH] = false

4. Operator calls unlockQueue(stETH, ...)
   → onlySupportedAsset(stETH) modifier: isSupportedAsset[stETH] == false → REVERT
   → nextLockedNonce[stETH] remains 0

5. Alice calls completeWithdrawal(stETH, "")
   → _processWithdrawalCompletion: nonce 0 >= nextLockedNonce[stETH] (0) → REVERT WithdrawalLocked

6. Alice calls sweepRemainingAssets(stETH)
   → onlySupportedAsset(stETH) → REVERT

Result: Alice's 10e18 rsETH is permanently frozen in LRTWithdrawalManager with no recovery path.
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-175)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
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

**File:** contracts/LRTWithdrawalManager.sol (L395-399)
```text
    function sweepRemainingAssets(address asset)
        external
        nonReentrant
        onlySupportedAsset(asset)
        onlyLRTManager
```

**File:** contracts/LRTWithdrawalManager.sol (L705-707)
```text
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/LRTConfig.sol (L80-84)
```text
        address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
            revert CannotRemoveAssetWithDeposits(asset);
        }
```

**File:** contracts/LRTConfig.sol (L86-93)
```text
        delete isSupportedAsset[asset];
        delete assetStrategy[asset];
        depositLimitByAsset[asset] = 0;

        supportedAssetList[tokenIndex] = supportedAssetList[supportedAssetList.length - 1];
        supportedAssetList.pop();

        emit RemovedSupportedAsset(asset);
```
