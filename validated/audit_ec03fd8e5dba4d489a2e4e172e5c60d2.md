After tracing the full code path, this is a real, concrete vulnerability. Here is the analysis:

---

### Title
Updating `LRT_WITHDRAW_MANAGER` in `LRTConfig` Permanently Freezes Pending Withdrawal Requests — (`contracts/LRTUnstakingVault.sol`)

---

### Summary

`LRTUnstakingVault.redeem()` guards access with `onlyLRTWithdrawalManager`, which dynamically resolves the authorized caller by reading `lrtConfig.withdrawManager()` at call time. If the admin updates the `LRT_WITHDRAW_MANAGER` address in `LRTConfig` while users have pending (still-locked) withdrawal requests in the old `LRTWithdrawalManager`, the old manager can never complete `unlockQueue()` — because its call to `unstakingVault.redeem()` will revert. Users' rsETH is permanently stranded in the old contract.

---

### Finding Description

**Step 1 — User initiates withdrawal on the old `LRTWithdrawalManager`:**

`initiateWithdrawal()` transfers rsETH from the user into the old `LRTWithdrawalManager` and records a locked withdrawal request. [1](#0-0) 

**Step 2 — Admin calls `LRTConfig.setContract(LRT_WITHDRAW_MANAGER, newAddr)`:**

This is a standard admin upgrade operation, gated only by `DEFAULT_ADMIN_ROLE`. No migration guard or pending-request check exists. [2](#0-1) 

**Step 3 — Operator tries to call `unlockQueue()` on the old `LRTWithdrawalManager`:**

`unlockQueue()` processes locked requests and then calls `unstakingVault.redeem()` to pull assets from the vault. [3](#0-2) 

**Step 4 — `LRTUnstakingVault.redeem()` reverts:**

The `onlyLRTWithdrawalManager` modifier resolves the authorized caller dynamically at call time via `lrtConfig.withdrawManager()`. After the config update, this now returns the **new** address. The old `LRTWithdrawalManager` is `msg.sender`, so the check fails and reverts. [4](#0-3) [5](#0-4) 

`lrtConfig.withdrawManager()` is resolved via: [6](#0-5) 

**Step 5 — Funds are permanently frozen:**

- The rsETH transferred to the old `LRTWithdrawalManager` during `initiateWithdrawal()` cannot be burned (that happens inside `unlockQueue()`).
- The underlying assets remain locked in `LRTUnstakingVault`.
- The new `LRTWithdrawalManager` has zero knowledge of the old requests.
- Users cannot call `completeWithdrawal()` on the old manager because the requests are still in "locked" state (`usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]` check would revert with `WithdrawalLocked`). [7](#0-6) 

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

All rsETH deposited into the old `LRTWithdrawalManager` for pending (not yet unlocked) withdrawal requests is permanently unrecoverable by users. The underlying assets in `LRTUnstakingVault` are also inaccessible. Recovery requires the admin to manually revert the `LRT_WITHDRAW_MANAGER` address back to the old contract, which may not happen if the team is unaware of the issue.

---

### Likelihood Explanation

**Low-Medium.** This does not require a malicious admin — it is triggered by a routine, legitimate upgrade of the withdrawal manager contract. Any protocol upgrade that replaces `LRTWithdrawalManager` while users have pending requests (which is the normal operating state) will trigger this. The withdrawal delay is ~8 days, meaning there will almost always be pending requests during any upgrade window. [8](#0-7) 

---

### Recommendation

Before allowing `setContract(LRT_WITHDRAW_MANAGER, newAddr)` to succeed, enforce that the old `LRTWithdrawalManager` has no pending locked requests (i.e., `nextLockedNonce[asset] == nextUnusedNonce[asset]` for all assets). Alternatively, the `onlyLRTWithdrawalManager` modifier in `LRTUnstakingVault` should store the authorized address as an immutable/storage variable set at initialization, rather than dynamically resolving it from config on every call.

---

### Proof of Concept

```solidity
// Fork test outline (local fork, no mainnet)
function test_freezeOnWithdrawManagerUpdate() public {
    // 1. User initiates withdrawal — rsETH transferred to oldWithdrawalManager
    vm.prank(user);
    oldWithdrawalManager.initiateWithdrawal(asset, rsETHAmount, "");

    // 2. Admin updates the withdrawal manager in LRTConfig
    vm.prank(admin);
    lrtConfig.setContract(LRTConstants.LRT_WITHDRAW_MANAGER, address(newWithdrawalManager));

    // 3. Operator tries to unlock the queue on the OLD manager
    vm.prank(operator);
    vm.expectRevert(ILRTUnstakingVault.CallerNotLRTWithdrawalManager.selector);
    oldWithdrawalManager.unlockQueue(
        asset, type(uint256).max, minAssetPrice, minRsEthPrice, maxAssetPrice, maxRsEthPrice
    );

    // 4. User cannot complete withdrawal either (still locked)
    vm.prank(user);
    vm.expectRevert(ILRTWithdrawalManager.WithdrawalLocked.selector);
    oldWithdrawalManager.completeWithdrawal(asset, "");

    // rsETH and underlying assets are permanently frozen
}
```

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```

**File:** contracts/LRTWithdrawalManager.sol (L166-176)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

```

**File:** contracts/LRTWithdrawalManager.sol (L301-307)
```text
        (rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
            asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
        );

        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L705-707)
```text
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```

**File:** contracts/LRTConfig.sol (L237-251)
```text
    function setContract(bytes32 contractKey, address contractAddress) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _setContract(contractKey, contractAddress);
    }

    /// @dev private function to set a contract
    /// @param key Contract key
    /// @param val Contract address
    function _setContract(bytes32 key, address val) private {
        UtilLib.checkNonZeroAddress(val);
        if (contractMap[key] == val) {
            revert ValueAlreadyInUse();
        }
        contractMap[key] = val;
        emit SetContract(key, val);
    }
```

**File:** contracts/LRTUnstakingVault.sol (L54-59)
```text
    modifier onlyLRTWithdrawalManager() {
        if (msg.sender != lrtConfig.withdrawManager()) {
            revert CallerNotLRTWithdrawalManager();
        }
        _;
    }
```

**File:** contracts/LRTUnstakingVault.sol (L99-105)
```text
    function redeem(address asset, uint256 amount) external nonReentrant onlyLRTWithdrawalManager {
        if (asset == LRTConstants.ETH_TOKEN) {
            ILRTWithdrawalManager(msg.sender).receiveFromLRTUnstakingVault{ value: amount }();
        } else {
            IERC20(asset).safeTransfer(msg.sender, amount);
        }
    }
```

**File:** contracts/utils/LRTConstants.sol (L93-95)
```text
    function withdrawManager(ILRTConfig config) internal view returns (address) {
        return config.getContract(LRT_WITHDRAW_MANAGER);
    }
```
