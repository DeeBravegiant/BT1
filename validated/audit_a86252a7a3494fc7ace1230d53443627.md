### Title
`LRTConfig.setContract()` Update of `LRT_WITHDRAW_MANAGER` Permanently Freezes Pending User Withdrawal Requests — (File: `contracts/LRTConfig.sol`)

---

### Summary

`LRTConfig.setContract()` can replace the `LRT_WITHDRAW_MANAGER` address at any time with no check for pending withdrawal state. When this happens, the old `LRTWithdrawalManager` is permanently severed from `LRTUnstakingVault`: the vault's `onlyLRTWithdrawalManager` modifier rejects calls from the old manager, so `unlockQueue` can never pull assets from the vault. Users who initiated withdrawals before the swap have their rsETH locked in the old manager with no recovery path.

---

### Finding Description

**Root cause — `LRTConfig.setContract()` has no guard for live withdrawal state:** [1](#0-0) 

The function accepts any new address for any contract key, including `LRT_WITHDRAW_MANAGER`, and immediately overwrites `contractMap[key]` with no check for pending user requests.

**The vault enforces a live lookup of the current withdrawal manager:** [2](#0-1) 

`onlyLRTWithdrawalManager` calls `lrtConfig.withdrawManager()` on every invocation. After `setContract` updates the key, the old manager's address no longer matches, and every call from it reverts.

**The only function that moves assets from vault to withdrawal manager is `unlockQueue`, which calls `unstakingVault.redeem()`:** [3](#0-2) 

If `redeem` reverts (because the old manager is rejected), `unlockQueue` cannot execute, `nextLockedNonce` is never advanced, and no withdrawal request can ever be marked unlocked.

**User rsETH is transferred into the old withdrawal manager at `initiateWithdrawal` time:** [4](#0-3) 

There is no function in `LRTWithdrawalManager` that allows a user to reclaim their rsETH once a request is created. The only exit is `completeWithdrawal`, which requires the request to first be unlocked via `unlockQueue`.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

Every user who called `initiateWithdrawal` before the `LRT_WITHDRAW_MANAGER` swap has:
- Their rsETH locked inside the old `LRTWithdrawalManager` (transferred in at line 166, not yet burned).
- No ability to complete the withdrawal (request is never unlocked because `unlockQueue` → `redeem` reverts).
- No ability to cancel or reclaim their rsETH (no such function exists).

The assets are permanently inaccessible. The old manager holds rsETH it cannot burn and cannot return; the vault holds the underlying assets it cannot release to the old manager.

---

### Likelihood Explanation

The `setContract` function is a standard admin operation used during protocol upgrades or contract replacements — exactly the scenario described in the external report. The protocol already uses `reinitializer` versioning (`initialize2`, `initialize3`) indicating active contract evolution. Any future replacement of the withdrawal manager (e.g., to add new features) triggers this freeze for all users with in-flight requests. No malicious intent is required; a routine upgrade is sufficient. [5](#0-4) 

---

### Recommendation

Before allowing `setContract` to overwrite `LRT_WITHDRAW_MANAGER`, enforce that the old manager has no pending (locked) withdrawal requests:

```solidity
// In LRTConfig.setContract or in a dedicated migration function:
ILRTWithdrawalManager oldManager = ILRTWithdrawalManager(contractMap[LRTConstants.LRT_WITHDRAW_MANAGER]);
require(oldManager.nextUnusedNonce(asset) == oldManager.nextLockedNonce(asset), "pending withdrawals exist");
```

Alternatively, add a migration path on `LRTWithdrawalManager` that allows users to reclaim their rsETH when the contract is decommissioned, analogous to the recommendation in the external report.

---

### Proof of Concept

1. Alice calls `LRTWithdrawalManager.initiateWithdrawal(stETH, 10e18, "")`. Her 10 rsETH is transferred to the old withdrawal manager. A withdrawal request is stored at nonce 0.
2. Admin calls `LRTConfig.setContract(LRT_WITHDRAW_MANAGER, newManagerAddress)`.
3. Operator calls `oldManager.unlockQueue(stETH, 1, ...)`. Inside, it calls `unstakingVault.redeem(stETH, amount)`.
4. `LRTUnstakingVault.redeem` checks `msg.sender != lrtConfig.withdrawManager()` → `oldManager != newManagerAddress` → **reverts with `CallerNotLRTWithdrawalManager`**.
5. `unlockQueue` reverts. `nextLockedNonce[stETH]` stays at 0.
6. Alice calls `oldManager.completeWithdrawal(stETH, "")`. The check `usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]` → `0 >= 0` → **reverts with `WithdrawalLocked`**.
7. Alice's 10 rsETH is permanently frozen in the old withdrawal manager with no recovery path. [6](#0-5) [7](#0-6)

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L109-121)
```text
    function initialize2(
        uint256 unlockedWithdrawalsCountETHx,
        uint256 unlockedWithdrawalsCountSTETH,
        uint256 unlockedWithdrawalsCountETH
    )
        external
        reinitializer(2)
        onlyRole(LRTConstants.UNLOCKED_WITHDRAWAL_INITIALIZER)
    {
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ST_ETH_TOKEN)] = unlockedWithdrawalsCountSTETH;
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ETHX_TOKEN)] = unlockedWithdrawalsCountETHx;
        unlockedWithdrawalsCount[LRTConstants.ETH_TOKEN] = unlockedWithdrawalsCountETH;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L305-307)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L700-707)
```text
        if (userAssociatedNonces[asset][user].empty()) {
            revert NoWithdrawalRequests(user, asset);
        }

        // Retrieve and remove the oldest withdrawal request for the user.
        uint256 usersFirstWithdrawalRequestNonce = userAssociatedNonces[asset][user].popFront();
        // Ensure the request is already unlocked.
        if (usersFirstWithdrawalRequestNonce >= nextLockedNonce[asset]) revert WithdrawalLocked();
```
