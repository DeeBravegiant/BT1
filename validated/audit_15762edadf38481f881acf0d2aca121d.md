### Title
`LRTConfig.removeSupportedAsset()` Does Not Check Pending Withdrawal Requests, Permanently Freezing Users' rsETH - (File: contracts/LRTConfig.sol)

---

### Summary

`LRTConfig.removeSupportedAsset()` guards against removal only by checking `LRTDepositPool.getTotalAssetDeposits(asset)`. It does not check whether `LRTWithdrawalManager` holds pending (locked) withdrawal requests for that asset. After removal, `LRTWithdrawalManager.unlockQueue()` is gated by `onlySupportedAsset`, which reads `lrtConfig.isSupportedAsset(asset)`. Once the asset is removed, `unlockQueue()` permanently reverts, and any rsETH already transferred into `LRTWithdrawalManager` via `initiateWithdrawal()` is frozen with no recovery path.

---

### Finding Description

`LRTConfig.removeSupportedAsset()` performs one safety check before deleting the asset's registry entries:

```solidity
if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
    revert CannotRemoveAssetWithDeposits(asset);
}
```

`getTotalAssetDeposits` aggregates balances across the deposit pool, NDCs, EigenLayer strategies, the converter, and the unstaking vault. It does **not** account for `LRTWithdrawalManager.assetsCommitted[asset]`, which tracks the underlying asset amount committed to pending (not yet unlocked) withdrawal requests.

The withdrawal lifecycle in `LRTWithdrawalManager` is:

1. **`initiateWithdrawal()`** — user's rsETH is transferred into `LRTWithdrawalManager`; `assetsCommitted[asset]` is incremented; the underlying asset remains in the unstaking vault.
2. **`unlockQueue()`** — operator redeems assets from the unstaking vault into `LRTWithdrawalManager`; rsETH is burned; `assetsCommitted[asset]` is decremented.
3. **`completeWithdrawal()`** — user receives the underlying asset.

A critical window exists between steps 1 and 2. During this window, the underlying assets are in the unstaking vault (counted by `getTotalAssetDeposits`). However, once a prior batch of requests has been processed through `unlockQueue()` and the unstaking vault is drained for that asset, `getTotalAssetDeposits` returns 0 for that asset even though new users may have already called `initiateWithdrawal()` (their rsETH is locked in `LRTWithdrawalManager`, `assetsCommitted[asset] > 0`).

In this state, `removeSupportedAsset()` passes its check and deletes `isSupportedAsset[asset]`. Subsequently:

- `unlockQueue()` carries `onlySupportedAsset(asset)`, which calls `lrtConfig.isSupportedAsset(asset)` — now `false` — and reverts with `AssetNotSupported`.
- `initiateWithdrawal()` also carries `onlySupportedAsset`, so no new requests can be made.
- `completeWithdrawal()` has **no** `onlySupportedAsset` guard, but it can only succeed for requests that have already been unlocked (step 2). Requests stuck between steps 1 and 2 can never be unlocked.
- There is no cancellation function in `LRTWithdrawalManager` that would return rsETH to users.

The rsETH transferred in `initiateWithdrawal()` is permanently frozen inside `LRTWithdrawalManager`.

---

### Impact Explanation

Users who called `initiateWithdrawal()` before the asset was removed have their rsETH permanently locked in `LRTWithdrawalManager` with no recovery path. `unlockQueue()` is the only mechanism to progress these requests, and it is permanently blocked by the `onlySupportedAsset` modifier after asset removal. This constitutes **permanent freezing of funds** (Critical).

---

### Likelihood Explanation

The scenario requires:
1. The protocol is winding down an asset (a realistic operational event).
2. A prior `unlockQueue()` batch has drained the unstaking vault for that asset, causing `getTotalAssetDeposits` to return 0 or below `maxNegligibleAmount`.
3. New users have called `initiateWithdrawal()` in the same block window (rsETH locked, `assetsCommitted > 0`, but underlying assets not yet in unstaking vault).
4. Admin calls `removeSupportedAsset()` without checking `LRTWithdrawalManager.assetsCommitted[asset]`.

This is a realistic operational mistake during asset deprecation. The admin has no on-chain prompt to check `assetsCommitted`, and the contract provides no guard. Likelihood is **Low-Medium**.

---

### Recommendation

Add a check in `removeSupportedAsset()` to verify that no pending withdrawal requests exist for the asset before deletion:

```diff
function removeSupportedAsset(address asset, uint256 tokenIndex)
    external
    onlySupportedAsset(asset)
    onlyRole(DEFAULT_ADMIN_ROLE)
{
    ...
    if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
        revert CannotRemoveAssetWithDeposits(asset);
    }

+   address withdrawalManager = getContract(LRTConstants.LRT_WITHDRAWAL_MANAGER);
+   if (ILRTWithdrawalManager(withdrawalManager).assetsCommitted(asset) > 0) {
+       revert CannotRemoveAssetWithPendingWithdrawals(asset);
+   }

    delete isSupportedAsset[asset];
    ...
}
```

---

### Proof of Concept

1. Asset `stETH` is being deprecated. All underlying stETH has been unstaked from EigenLayer and moved to the unstaking vault. A prior `unlockQueue()` call processes all existing requests, draining the unstaking vault. `getTotalAssetDeposits(stETH)` now returns 0.

2. Alice calls `initiateWithdrawal(stETH, 10e18, "")`. Her 10 rsETH is transferred to `LRTWithdrawalManager`. `assetsCommitted[stETH] += expectedStETH`. The underlying stETH is not yet in the unstaking vault (it will be redeemed during the next `unlockQueue()` call).

3. Admin calls `LRTConfig.removeSupportedAsset(stETH, 0)`. The check `getTotalAssetDeposits(stETH) > maxNegligibleAmount` passes (returns 0). `isSupportedAsset[stETH]` is deleted.

4. Operator attempts `unlockQueue(stETH, ...)`. The `onlySupportedAsset(stETH)` modifier calls `lrtConfig.isSupportedAsset(stETH)` → `false` → reverts with `AssetNotSupported`.

5. Alice's 10 rsETH is permanently locked in `LRTWithdrawalManager`. There is no cancellation function. `completeWithdrawal()` cannot succeed because the request was never unlocked.

---

**Root cause references:**

`removeSupportedAsset()` check (does not include `assetsCommitted`): [1](#0-0) 

`isSupportedAsset` deleted on removal: [2](#0-1) 

`unlockQueue()` blocked by `onlySupportedAsset` after removal: [3](#0-2) 

`onlySupportedAsset` reads `lrtConfig.isSupportedAsset`: [4](#0-3) 

rsETH locked in `initiateWithdrawal()` with no cancel path: [5](#0-4) 

`assetsCommitted` not checked in `removeSupportedAsset()`: [6](#0-5)

### Citations

**File:** contracts/LRTConfig.sol (L80-84)
```text
        address depositPool = getContract(LRTConstants.LRT_DEPOSIT_POOL);

        if (ILRTDepositPool(depositPool).getTotalAssetDeposits(asset) > maxNegligibleAmount) {
            revert CannotRemoveAssetWithDeposits(asset);
        }
```

**File:** contracts/LRTConfig.sol (L86-88)
```text
        delete isSupportedAsset[asset];
        delete assetStrategy[asset];
        depositLimitByAsset[asset] = 0;
```

**File:** contracts/LRTWithdrawalManager.sol (L52-53)
```text
    // Asset amount committed to be withdrawn by users.
    mapping(address asset => uint256 amount) public assetsCommitted;
```

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

**File:** contracts/utils/LRTConfigRoleChecker.sol (L65-70)
```text
    modifier onlySupportedAsset(address asset) {
        if (!lrtConfig.isSupportedAsset(asset)) {
            revert ILRTConfig.AssetNotSupported();
        }
        _;
    }
```
