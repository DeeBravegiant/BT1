### Title
ETH Withdrawal Permanently Frozen for Smart Contract Depositors Without `receive()` — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager._processWithdrawalCompletion` unconditionally pushes ETH to the user's address via a low-level `call`. If the user is a smart contract that has no `receive()` or `fallback()` function, every attempt to complete the withdrawal reverts. Because the user's rsETH was already burned during `unlockQueue`, the ETH is permanently stranded in the contract with no admin rescue path.

---

### Finding Description

The withdrawal lifecycle is:

1. **`initiateWithdrawal`** — user's rsETH is pulled into `LRTWithdrawalManager`.
2. **`unlockQueue`** (operator) — rsETH is burned; `unlockedWithdrawalsCount[asset]` is incremented; the request is marked unlocked.
3. **`completeWithdrawal` / `completeWithdrawalForUser`** — calls `_processWithdrawalCompletion`, which pushes the asset to the user.

`_processWithdrawalCompletion` delegates to `_transferAsset`:

```solidity
// LRTWithdrawalManager.sol line 876-883
function _transferAsset(address asset, address to, uint256 amount) internal {
    if (asset == LRTConstants.ETH_TOKEN) {
        (bool sent,) = payable(to).call{ value: amount }("");
        if (!sent) revert EthTransferFailed();
    } else {
        IERC20(asset).safeTransfer(to, amount);
    }
}
``` [1](#0-0) 

For ETH withdrawals, if `to` is a smart contract without a `receive()` function, `payable(to).call{value: amount}("")` returns `false`, causing `EthTransferFailed` to revert the entire transaction. Because the revert unwinds all state changes (the `popFront`, the `delete withdrawalRequests[requestId]`, and the `unlockedWithdrawalsCount[asset]--` at line 717), the withdrawal request is fully restored on every attempt — but the transfer will fail again on every subsequent call. [2](#0-1) 

The rsETH was already burned during `unlockQueue` (before `completeWithdrawal` is ever called), so the user has permanently lost their rsETH. The ETH sits in `LRTWithdrawalManager` indefinitely.

There is no admin escape hatch. `sweepRemainingAssets` is gated by:

```solidity
if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();
``` [3](#0-2) 

Because `unlockedWithdrawalsCount[ETH]` is always restored by the revert, `hasUnlockedWithdrawals(ETH)` remains `true`, and `sweepRemainingAssets` can never be called for ETH. No other function in the contract can redirect or rescue the frozen ETH.

The `completeWithdrawalForUser` operator path suffers the same failure — the developer comment even acknowledges the ETH push risk ("Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH"), but misclassifies it as non-impactful when the recipient is a non-payable contract. [4](#0-3) 

---

### Impact Explanation

**Permanent freezing of funds.** A smart contract depositor (e.g., a multisig, DAO treasury, or vault contract) that holds rsETH, initiates an ETH withdrawal, and lacks a `receive()` function will have its ETH permanently locked in `LRTWithdrawalManager`. The rsETH is already burned; neither the user nor any operator can recover the ETH. This matches the Critical impact tier: permanent freezing of user funds.

---

### Likelihood Explanation

**Low.** The scenario requires a smart contract without a `receive()` function to (a) hold rsETH, (b) initiate an ETH withdrawal, and (c) have its withdrawal unlocked. Many protocol-level contracts (Gnosis Safe multisigs with certain module configurations, immutable vault contracts, proxy contracts without ETH fallback) satisfy condition (a) and (b) without satisfying (c) by design. The likelihood is low but non-negligible given the protocol's institutional user base.

---

### Recommendation

Apply the pull-over-push pattern for ETH withdrawals: instead of pushing ETH to the user in `_processWithdrawalCompletion`, record the claimable amount in a mapping and let the user (or any caller on their behalf) pull it via a separate `claimETH(address user)` function. Alternatively, add an admin-only `rescueStuckWithdrawal(address asset, address user, address recipient)` function that can redirect a permanently stuck withdrawal to a different address, bypassing the frozen recipient.

---

### Proof of Concept

1. `VaultContract` (no `receive()`) holds rsETH and calls `initiateWithdrawal(ETH_TOKEN, amount, "")`. rsETH is transferred to `LRTWithdrawalManager`.
2. Operator calls `unlockQueue(ETH_TOKEN, ...)`. rsETH is burned; `unlockedWithdrawalsCount[ETH]` becomes 1.
3. `VaultContract` calls `completeWithdrawal(ETH_TOKEN, "")`. Inside `_processWithdrawalCompletion`, `_transferAsset` executes `payable(VaultContract).call{value: amount}("")`. The call returns `false` (no `receive()`). `EthTransferFailed` is thrown; the entire transaction reverts. `unlockedWithdrawalsCount[ETH]` is restored to 1.
4. Operator calls `completeWithdrawalForUser(ETH_TOKEN, VaultContract, "")`. Same revert.
5. Operator attempts `sweepRemainingAssets(ETH_TOKEN)`. Reverts with `PendingWithdrawalsExist` because `unlockedWithdrawalsCount[ETH] == 1`.
6. ETH is permanently locked in `LRTWithdrawalManager`. `VaultContract`'s rsETH is gone.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L187-204)
```text
    /// @notice Allows operators to complete a user's withdrawal process
    /// @param asset The asset address the user wishes to withdraw
    /// @param user The address of the user whose withdrawal to complete
    /// @param referralId The referral identifier for tracking
    /// @dev Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH
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
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L402-403)
```text
        // Check that all withdrawals are completed
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();
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
