### Title
Blacklisted User's LST Funds Permanently Frozen in `LRTWithdrawalManager` After `unlockQueue` Burns rsETH - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager` provides no mechanism for a user to redirect their queued withdrawal to an alternative recipient address. If a user is blacklisted by a supported LST asset contract after their rsETH has been burned by `unlockQueue`, the corresponding LST amount is permanently frozen inside the contract with no recovery path.

---

### Finding Description

The withdrawal lifecycle in `LRTWithdrawalManager` proceeds in three steps:

**Step 1 — `initiateWithdrawal`**: The user's rsETH is pulled into the contract and a withdrawal request is recorded against `msg.sender`. [1](#0-0) 

**Step 2 — `unlockQueue`** (operator-triggered): The rsETH held by the contract is **permanently burned**, and the corresponding LST amount is redeemed from the unstaking vault into the withdrawal manager. [2](#0-1) 

**Step 3 — `completeWithdrawal`**: The LST is transferred to the original requester's address. The recipient is always hardcoded to `user` (i.e., the original `msg.sender` from step 1) with no way to override it. [3](#0-2) [4](#0-3) 

The internal `_transferAsset` function calls `IERC20(asset).safeTransfer(to, amount)` for ERC20 assets: [5](#0-4) 

If the user is blacklisted by the LST contract between steps 1 and 3, every call to `completeWithdrawal` will revert because `safeTransfer` to a blacklisted address reverts. The operator-assisted `completeWithdrawalForUser` does not help — it still sends to the original `user` address: [6](#0-5) 

There is no function in the contract that allows the user (or anyone on their behalf) to redirect the LST payout to a different address. The rsETH has already been burned in step 2 and cannot be recovered. The LST is permanently locked in the contract.

The same design flaw exists in `instantWithdrawal`, which also hardcodes `msg.sender` as the recipient: [7](#0-6) 

However, for `instantWithdrawal` the rsETH burn and the LST transfer occur in the same transaction, so a revert on the transfer also reverts the burn — the user's rsETH is not lost, only the withdrawal attempt fails. The critical path is the **queued withdrawal** where the burn is irreversible before the transfer is attempted.

---

### Impact Explanation

After `unlockQueue` executes, the user's rsETH is permanently destroyed and the LST sits in `LRTWithdrawalManager`. If the user's address is subsequently blacklisted by the LST contract, `completeWithdrawal` will always revert and the LST is permanently frozen. The user loses the full principal value of their rsETH with no recovery mechanism. This matches the **Critical — Permanent freezing of funds** impact category.

---

### Likelihood Explanation

The currently supported LST assets (stETH, ETHx, sfrxETH) do not implement address blacklisting. However, the protocol's asset list is governed and extensible — `lrtConfig.getSupportedAssetList()` can include new assets. Any future supported asset that implements blacklisting (e.g., a USDC-backed LST or a regulated token) would make this exploitable. Consistent with the external report, likelihood is **Low**, yielding an overall **Medium** severity.

---

### Recommendation

Add an optional `recipient` parameter to `completeWithdrawal` (and `instantWithdrawal`) so that the caller can redirect the payout to an address they control that is not blacklisted:

```diff
- function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
-     _processWithdrawalCompletion(asset, msg.sender, referralId);
+ function completeWithdrawal(address asset, address recipient, string calldata referralId) external nonReentrant whenNotPaused {
+     address to = (recipient == address(0)) ? msg.sender : recipient;
+     _processWithdrawalCompletion(asset, msg.sender, to, referralId);
  }
```

Inside `_processWithdrawalCompletion`, verify the caller owns the request (via `userAssociatedNonces[asset][msg.sender]`) but transfer to the specified `recipient`. Apply the same pattern to `instantWithdrawal`.

---

### Proof of Concept

1. Alice holds rsETH and calls `initiateWithdrawal(stETH, 1e18, "")`. Her rsETH is transferred to `LRTWithdrawalManager`; a withdrawal request is stored under `userAssociatedNonces[stETH][Alice]`.
2. The operator calls `unlockQueue(stETH, ...)`. Alice's rsETH is burned at line 305; the corresponding stETH is redeemed into `LRTWithdrawalManager` at line 307. Alice's rsETH is now gone.
3. Alice's address is blacklisted by the stETH contract (e.g., due to regulatory action).
4. Alice calls `completeWithdrawal(stETH, "")`. `_processWithdrawalCompletion` reaches line 734: `_transferAsset(stETH, Alice, amount)` → `IERC20(stETH).safeTransfer(Alice, amount)` → **reverts** because Alice is blacklisted.
5. The operator calls `completeWithdrawalForUser(stETH, Alice, "")`. Same result — still sends to `Alice` at line 734 → **reverts**.
6. No other function exists to claim Alice's stETH. The funds are permanently frozen in `LRTWithdrawalManager`. [8](#0-7)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-176)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);

```

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L192-203)
```text
    function completeWithdrawalForUser(
        address asset,
        address user,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlyLRTOperator
    {
        _processWithdrawalCompletion(asset, user, referralId);
        emit AssetWithdrawalCompletedBy(msg.sender);
```

**File:** contracts/LRTWithdrawalManager.sol (L250-250)
```text
        _transferAsset(asset, msg.sender, userAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L305-307)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L699-738)
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

        // If Aave integration is enabled and asset is ETH, withdraw from Aave if needed
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN) {
            uint256 contractBalance = address(this).balance;
            if (contractBalance < request.expectedAssetAmount) {
                uint256 amountNeeded = request.expectedAssetAmount - contractBalance;
                _withdrawFromAave(amountNeeded);

                // Verify we have sufficient balance after withdrawal
                uint256 balanceAfter = address(this).balance;
                if (balanceAfter < request.expectedAssetAmount) {
                    revert InsufficientLiquidityForWithdrawal();
                }
            }
        }

        _transferAsset(asset, user, request.expectedAssetAmount);

        emit ReferralIdEmitted(referralId);
        emit AssetWithdrawalFinalized(user, asset, request.rsETHUnstaked, request.expectedAssetAmount);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L876-883)
```text
    function _transferAsset(address asset, address to, uint256 amount) internal {
        if (asset == LRTConstants.ETH_TOKEN) {
            (bool sent,) = payable(to).call{ value: amount }("");
            if (!sent) revert EthTransferFailed();
        } else {
            IERC20(asset).safeTransfer(to, amount);
        }
    }
```
