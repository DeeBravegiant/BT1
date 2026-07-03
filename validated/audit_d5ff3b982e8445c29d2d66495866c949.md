### Title
`LRTConfig.setRSETH()` Can Be Called While Pending Withdrawal Requests Exist, Permanently Freezing User rsETH Funds - (File: contracts/LRTConfig.sol)

---

### Summary

`LRTConfig.setRSETH()` allows the admin to replace the `rsETH` token address at any time without checking whether `LRTWithdrawalManager` holds pending withdrawal requests. Because `LRTWithdrawalManager` reads `lrtConfig.rsETH()` dynamically at call time in both `initiateWithdrawal()` and `unlockQueue()`, updating the rsETH address mid-flight causes `unlockQueue()` to attempt burning tokens from the new rsETH contract while the contract only holds old rsETH tokens. The burn reverts, all pending withdrawal requests become permanently unresolvable, and users' rsETH is locked in the contract with no recovery path.

---

### Finding Description

**Step 1 — User initiates withdrawal:**
`LRTWithdrawalManager.initiateWithdrawal()` pulls rsETH from the user into the contract using the current `lrtConfig.rsETH()`:

```solidity
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

The rsETH tokens now sit in `LRTWithdrawalManager`. The withdrawal request is stored keyed by `asset` (the LST/ETH the user wants to receive), not by the rsETH address. [1](#0-0) 

**Step 2 — Admin updates rsETH address:**
`LRTConfig.setRSETH()` replaces `rsETH` with no guard against pending withdrawal requests:

```solidity
function setRSETH(address rsETH_) external onlyRole(DEFAULT_ADMIN_ROLE) {
    UtilLib.checkNonZeroAddress(rsETH_);
    rsETH = rsETH_;
    emit SetRSETH(rsETH_);
}
``` [2](#0-1) 

**Step 3 — Operator calls `unlockQueue()`, which now reads the new rsETH address:**

```solidity
if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
```

`lrtConfig.rsETH()` now returns the **new** rsETH contract. The `LRTWithdrawalManager` holds **old** rsETH tokens and has zero balance in the new rsETH contract. The `burnFrom` call reverts. No withdrawal request can ever be unlocked. [3](#0-2) 

**Step 4 — No recovery path:**
`completeWithdrawal()` requires the request to be unlocked first (via `unlockQueue()`). Since `unlockQueue()` permanently reverts, users can never reach `completeWithdrawal()`. There is no function to return the locked rsETH tokens to users. The old rsETH tokens are permanently stranded in `LRTWithdrawalManager`. [4](#0-3) 

---

### Impact Explanation

All rsETH deposited into `LRTWithdrawalManager` at the time of the `setRSETH()` call is permanently frozen. Users cannot complete or cancel their withdrawal requests. The old rsETH tokens have no recovery mechanism inside the contract. This constitutes **permanent freezing of funds**. [5](#0-4) 

---

### Likelihood Explanation

`setRSETH()` requires `DEFAULT_ADMIN_ROLE`, making this a low-frequency operation. However, no on-chain enforcement prevents it from being called while withdrawals are pending — the admin has no visibility into pending requests from within `setRSETH()` itself. A legitimate protocol upgrade (e.g., deploying a new rsETH token) performed without first draining the withdrawal queue would silently trigger this freeze. Likelihood is **Low**, but the impact is **Critical** (permanent fund freeze). [2](#0-1) 

---

### Recommendation

Add a guard in `setRSETH()` that requires the `LRTWithdrawalManager` to have no pending (locked) withdrawal requests across all supported assets before the rsETH address can be updated. Concretely, check that `nextUnusedNonce[asset] == nextLockedNonce[asset]` for every supported asset, or introduce a dedicated `pendingWithdrawalsExist()` view on `LRTWithdrawalManager` and require it to return `false`:

```solidity
function setRSETH(address rsETH_) external onlyRole(DEFAULT_ADMIN_ROLE) {
    UtilLib.checkNonZeroAddress(rsETH_);
    // Ensure no rsETH is held in-flight by the withdrawal manager
    ILRTWithdrawalManager wm = ILRTWithdrawalManager(
        contractMap[LRTConstants.LRT_WITHDRAW_MANAGER]
    );
    require(!wm.hasPendingWithdrawals(), "Pending withdrawals exist");
    rsETH = rsETH_;
    emit SetRSETH(rsETH_);
}
```

Alternatively, require the `LRTWithdrawalManager` to be paused before `setRSETH()` can execute.

---

### Proof of Concept

1. Alice calls `LRTWithdrawalManager.initiateWithdrawal(stETH, 100e18, "")`. The contract receives 100 old-rsETH. Her request is stored with `rsETHUnstaked = 100e18`.
2. Admin calls `LRTConfig.setRSETH(newRsETH)`. `lrtConfig.rsETH()` now returns `newRsETH`.
3. Operator calls `LRTWithdrawalManager.unlockQueue(stETH, ...)`. Inside, `IRSETH(lrtConfig.rsETH()).burnFrom(address(this), 100e18)` is called on `newRsETH`. The `LRTWithdrawalManager` has zero balance of `newRsETH` → `burnFrom` reverts.
4. Alice calls `completeWithdrawal(stETH, "")` → reverts with `WithdrawalLocked` because `nextLockedNonce` was never advanced.
5. Alice's 100 old-rsETH tokens are permanently locked in `LRTWithdrawalManager` with no recovery path. [6](#0-5) [7](#0-6)

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

**File:** contracts/LRTWithdrawalManager.sol (L183-184)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
```

**File:** contracts/LRTWithdrawalManager.sol (L300-307)
```text
        // utilized.
        (rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
            asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
        );

        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L699-717)
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

        unlockedWithdrawalsCount[asset]--;
```

**File:** contracts/LRTConfig.sol (L215-219)
```text
    function setRSETH(address rsETH_) external onlyRole(DEFAULT_ADMIN_ROLE) {
        UtilLib.checkNonZeroAddress(rsETH_);
        rsETH = rsETH_;
        emit SetRSETH(rsETH_);
    }
```
