### Title
Updating `LRT_WITHDRAW_MANAGER` in `LRTConfig` Permanently Freezes Pending User rsETH - (File: contracts/LRTConfig.sol, contracts/LRTUnstakingVault.sol, contracts/LRTWithdrawalManager.sol)

---

### Summary

When a user calls `initiateWithdrawal()` on `LRTWithdrawalManager`, their rsETH is transferred into that contract and a withdrawal request is recorded in its state. If the admin subsequently updates the `LRT_WITHDRAW_MANAGER` reference in `LRTConfig` (via `setContract()`), the old withdrawal manager loses its authorization to call `LRTUnstakingVault.redeem()`. Because `unlockQueue()` must succeed before `completeWithdrawal()` can be called, all pending withdrawal requests in the old manager become permanently uncompletable, and users' rsETH is frozen with no cancellation path.

---

### Finding Description

**Step 1 — User initiates withdrawal:**

In `LRTWithdrawalManager.initiateWithdrawal()`, the user's rsETH is pulled into the withdrawal manager contract and a `WithdrawalRequest` is stored in its state:

```solidity
IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
// ...
_addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
``` [1](#0-0) 

**Step 2 — Admin updates the withdrawal manager reference:**

`LRTConfig.setContract()` is callable by `DEFAULT_ADMIN_ROLE` and can overwrite the `LRT_WITHDRAW_MANAGER` entry in `contractMap` at any time, with no check for pending withdrawal requests:

```solidity
function setContract(bytes32 contractKey, address contractAddress) external onlyRole(DEFAULT_ADMIN_ROLE) {
    _setContract(contractKey, contractAddress);
}
``` [2](#0-1) 

**Step 3 — Old withdrawal manager is rejected by `LRTUnstakingVault.redeem()`:**

`LRTUnstakingVault.redeem()` is gated by `onlyLRTWithdrawalManager`, which performs a live lookup of the current withdrawal manager from `lrtConfig`:

```solidity
modifier onlyLRTWithdrawalManager() {
    if (msg.sender != lrtConfig.withdrawManager()) {
        revert CallerNotLRTWithdrawalManager();
    }
    _;
}
``` [3](#0-2) 

After the update, `lrtConfig.withdrawManager()` returns the new address. Any call from the old withdrawal manager to `unstakingVault.redeem()` inside `unlockQueue()` will revert. [4](#0-3) 

**Step 4 — `completeWithdrawal()` is permanently blocked:**

`_processWithdrawalCompletion()` requires that the request's nonce is below `nextLockedNonce[asset]`, which is only advanced by `_unlockWithdrawalRequests()` inside `unlockQueue()`. Since `unlockQueue()` now always reverts (because `unstakingVault.redeem()` rejects the old manager), `nextLockedNonce` is never advanced, and `completeWithdrawal()` always reverts with `WithdrawalLocked`:

```solidity
if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
``` [5](#0-4) 

There is no `cancelWithdrawal()` or refund function in `LRTWithdrawalManager`. The rsETH held in the old contract has no exit path for users.

---

### Impact Explanation

Users who called `initiateWithdrawal()` before the admin updated `LRT_WITHDRAW_MANAGER` have their rsETH permanently frozen in the old `LRTWithdrawalManager` contract. There is no on-chain mechanism for them to cancel the request and recover their tokens. The only recovery path would require the admin to perform an additional UUPS upgrade of the old withdrawal manager to add a rescue function — this is not guaranteed and is not part of the protocol's documented behavior.

**Impact: Permanent freezing of user funds (rsETH). Critical.**

---

### Likelihood Explanation

The admin legitimately needs to upgrade `LRTWithdrawalManager` during protocol evolution (e.g., to add features, fix bugs, or integrate new vault logic). The `setContract()` function has no guard that checks for pending withdrawal requests before allowing the update. Given that the withdrawal queue operates continuously and the delay is 8 days (`withdrawalDelayBlocks = 8 days / 12 seconds`), there will almost always be pending requests in the queue at any upgrade time. This makes the scenario realistic during any protocol upgrade cycle.

**Likelihood: Medium.**

---

### Recommendation

Before allowing `setContract(LRT_WITHDRAW_MANAGER, ...)` to succeed, verify that the old withdrawal manager has no pending (locked) withdrawal requests:

```solidity
// In LRTConfig.setContract or in a dedicated migration function:
ILRTWithdrawalManager oldManager = ILRTWithdrawalManager(contractMap[LRT_WITHDRAW_MANAGER]);
require(!oldManager.hasAnyPendingWithdrawals(), "Pending withdrawals exist");
```

Alternatively, add a `cancelWithdrawal()` function to `LRTWithdrawalManager` that allows users to reclaim their rsETH before the manager is replaced, analogous to the Nouns DAO recommendation of allowing users to call `returnTokensToOwner` directly.

---

### Proof of Concept

1. User calls `LRTWithdrawalManager.initiateWithdrawal(stETH, 1e18, "")`. Their 1e18 rsETH is transferred to the old withdrawal manager (`WM_old`). A `WithdrawalRequest` is stored at nonce 0. [6](#0-5) 

2. Admin calls `LRTConfig.setContract(LRT_WITHDRAW_MANAGER, WM_new)`. `lrtConfig.withdrawManager()` now returns `WM_new`. [2](#0-1) 

3. Operator calls `WM_old.unlockQueue(stETH, ...)`. Inside, `unstakingVault.redeem(stETH, amount)` is called. The `onlyLRTWithdrawalManager` modifier checks `msg.sender != lrtConfig.withdrawManager()` → `WM_old != WM_new` → **reverts with `CallerNotLRTWithdrawalManager`**. [7](#0-6) 

4. User calls `WM_old.completeWithdrawal(stETH, "")`. `nextLockedNonce[stETH]` is still 0, user's nonce is 0, so `0 >= 0` → **reverts with `WithdrawalLocked`**. [8](#0-7) 

5. User's 1e18 rsETH is permanently locked in `WM_old` with no recovery path.

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

**File:** contracts/LRTWithdrawalManager.sol (L283-307)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));

        UnlockParams memory params = _createUnlockParams(lrtOracle, unstakingVault, asset);

        _validatePrices(
            params.rsETHPrice,
            params.assetPrice,
            minimumRsEthPrice,
            maximumRsEthPrice,
            minimumAssetPrice,
            maximumAssetPrice
        );

        if (params.totalAvailableAssets == 0) revert AmountMustBeGreaterThanZero();

        // Updates and unlocks withdrawal requests up to a specified upper limit or until allocated assets are fully
        // utilized.
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
