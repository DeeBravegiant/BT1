### Title
ETH Withdrawal Permanently Frozen for Contract Recipients That Revert on ETH Receive - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager` uses a push-payment model to deliver ETH to withdrawing users. If a user's address is a contract that reverts on ETH receipt, `completeWithdrawal` will always revert for that user. Because rsETH is burned in a prior, separate transaction (`unlockQueue`), the user's rsETH is permanently destroyed while their ETH remains frozen inside `LRTWithdrawalManager` with no recovery path.

### Finding Description
The internal helper `_transferAsset` delivers ETH via a low-level call:

```solidity
// contracts/LRTWithdrawalManager.sol L876-879
function _transferAsset(address asset, address to, uint256 amount) internal {
    if (asset == LRTConstants.ETH_TOKEN) {
        (bool sent,) = payable(to).call{ value: amount }("");
        if (!sent) revert EthTransferFailed();
    }
```

This is called from `_processWithdrawalCompletion` (line 734), which is the terminal step of both `completeWithdrawal` (line 183) and `completeWithdrawalForUser` (line 192).

The critical ordering is across **two separate transactions**:

**Transaction 1 – `unlockQueue`** (lines 301–307):
```solidity
(rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(...);
if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
unstakingVault.redeem(asset, assetAmountUnlocked);
```
rsETH is **permanently burned** and ETH is moved into `LRTWithdrawalManager`.

**Transaction 2 – `completeWithdrawal`** (line 183 → line 734):
```solidity
_transferAsset(asset, user, request.expectedAssetAmount);
```
If `user` is a contract with no `receive()` or a reverting fallback, this call fails, the transaction reverts, and the withdrawal request is restored to the queue. The user can retry indefinitely, but every attempt will revert.

Because the rsETH burn happened in Transaction 1 (already finalized on-chain), it is **not undone** by the revert in Transaction 2. The user's rsETH is gone and the ETH is stuck.

Recovery via `sweepRemainingAssets` is also blocked:
```solidity
// L403
if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();
```
`unlockedWithdrawalsCount[asset]` remains > 0 because the stuck withdrawal is still counted as unlocked, so the manager can never sweep the ETH to treasury either.

### Impact Explanation
A user who initiated a withdrawal from a smart contract address (e.g., a multisig, a smart-contract wallet, or any contract without a `receive()` function) will have their rsETH permanently burned while their corresponding ETH is frozen inside `LRTWithdrawalManager` with no on-chain mechanism to recover it. This constitutes permanent freezing of user funds.

### Likelihood Explanation
Smart-contract wallets, multisigs (e.g., Gnosis Safe), and protocol-owned treasuries routinely hold rsETH and may initiate withdrawals. Many such contracts do not implement a `receive()` function or implement one that reverts under certain conditions. The protocol's own `completeWithdrawalForUser` operator path (line 192) is explicitly documented as "not expected to be used for ETH" due to related gas-grief concerns, confirming the developers are aware of the fragility of ETH push-delivery to arbitrary addresses.

### Recommendation
Replace the push-payment model for ETH withdrawals with a pull-payment (claimable) model:
- In `_processWithdrawalCompletion`, record the owed ETH amount in a `mapping(address => uint256) public claimableETH` instead of immediately sending it.
- Add a separate `claimETH()` function that lets the user pull their ETH at any time.
- This eliminates the dependency on the recipient's ability to receive ETH and mirrors the recommendation in the referenced external report.

### Proof of Concept
1. A smart-contract wallet (no `receive()`) holds rsETH and calls `initiateWithdrawal(ETH_TOKEN, rsETHAmount, "")`. rsETH is transferred to `LRTWithdrawalManager`.
2. Operator calls `unlockQueue(ETH_TOKEN, ...)`. rsETH is **burned** (`burnFrom` at line 305) and ETH is redeemed from `LRTUnstakingVault` into `LRTWithdrawalManager` (line 307). This transaction is finalized.
3. The smart-contract wallet calls `completeWithdrawal(ETH_TOKEN, "")`. Inside `_processWithdrawalCompletion`, `_transferAsset` executes `payable(wallet).call{ value: amount }("")`. The wallet has no `receive()`, so the call returns `success = false`. `revert EthTransferFailed()` is triggered; the entire transaction reverts.
4. The withdrawal request is restored to the queue (revert undoes in-tx state). rsETH remains burned (prior tx). ETH remains in `LRTWithdrawalManager`.
5. Every subsequent call to `completeWithdrawal` or `completeWithdrawalForUser` for this user repeats step 3 and reverts.
6. `sweepRemainingAssets` is permanently blocked because `unlockedWithdrawalsCount[ETH_TOKEN] > 0`.
7. The user's ETH is permanently frozen with no recovery path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L183-204)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }

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

**File:** contracts/LRTWithdrawalManager.sol (L301-307)
```text
        (rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
            asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
        );

        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L395-413)
```text
    function sweepRemainingAssets(address asset)
        external
        nonReentrant
        onlySupportedAsset(asset)
        onlyLRTManager
        returns (uint256 transferredAmount)
    {
        // Check that all withdrawals are completed
        if (hasUnlockedWithdrawals(asset)) revert PendingWithdrawalsExist();

        uint256 balance = _getAssetBalance(asset);
        if (balance == 0) revert AmountMustBeGreaterThanZero();

        // Transfer to treasury
        address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        _transferAsset(asset, treasury, balance);

        emit RemainingAssetsSwept(asset, balance, treasury);
        return balance;
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
