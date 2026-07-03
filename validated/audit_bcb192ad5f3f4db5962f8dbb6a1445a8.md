### Title
ETH Transfer to Contract Recipient Without `receive` Permanently Freezes User Funds After rsETH Burn - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager._transferAsset` sends ETH to a user address via a low-level `.call{value}`. If the recipient is a smart contract without a `receive` or `fallback` function, the call fails and the transaction reverts. Because rsETH is burned in a prior, separate `unlockQueue` transaction, the user's rsETH is permanently destroyed while their ETH allocation is permanently frozen inside `LRTWithdrawalManager`.

### Finding Description
`_transferAsset` is the sole ETH disbursement primitive in `LRTWithdrawalManager`:

```solidity
// contracts/LRTWithdrawalManager.sol L876-883
function _transferAsset(address asset, address to, uint256 amount) internal {
    if (asset == LRTConstants.ETH_TOKEN) {
        (bool sent,) = payable(to).call{ value: amount }("");
        if (!sent) revert EthTransferFailed();
    } else {
        IERC20(asset).safeTransfer(to, amount);
    }
}
``` [1](#0-0) 

It is called from `_processWithdrawalCompletion` to deliver ETH to the withdrawing user:

```solidity
// contracts/LRTWithdrawalManager.sol L734
_transferAsset(asset, user, request.expectedAssetAmount);
``` [2](#0-1) 

The withdrawal lifecycle spans **two separate transactions**:

1. **`initiateWithdrawal`** — user's rsETH is pulled into `LRTWithdrawalManager` and a `WithdrawalRequest` is recorded.
2. **`unlockQueue`** (operator) — rsETH held by the contract is **burned** (`IRSETH.burnFrom`) and ETH is redeemed from `LRTUnstakingVault` into `LRTWithdrawalManager`.
3. **`completeWithdrawal` / `completeWithdrawalForUser`** — attempts to push ETH to the user. [3](#0-2) [4](#0-3) 

After step 2 completes, the rsETH no longer exists. In step 3, if `user` is a contract without a `receive` function, `payable(to).call{value: amount}("")` returns `false`, `EthTransferFailed` is thrown, and the entire `completeWithdrawal` transaction reverts. Because the state changes in `_processWithdrawalCompletion` (queue pop, request deletion, counter decrement) are all reverted, the request remains in the queue — but every future attempt to complete it will also revert for the same reason. The ETH is permanently locked.

The operator-callable `completeWithdrawalForUser` path is equally affected since it calls the same `_processWithdrawalCompletion` internal: [5](#0-4) 

### Impact Explanation
**Critical — Permanent freezing of funds.**

The user's rsETH is irreversibly burned in `unlockQueue`. The corresponding ETH allocation sits in `LRTWithdrawalManager` but can never be delivered. There is no recovery path: no admin function exists to redirect a locked withdrawal to an alternative address, and the `sweepRemainingAssets` function is blocked while `unlockedWithdrawalsCount[asset] > 0`. [6](#0-5) 

### Likelihood Explanation
**Medium.** Smart-contract wallets, protocol treasuries, EigenLayer operator contracts, and DeFi vaults routinely interact with LRT protocols. Any such contract that holds rsETH and calls `initiateWithdrawal` without implementing `receive() external payable` will trigger this freeze. The pattern is well-known and has been exploited in analogous protocols.

### Recommendation
Replace the push-ETH pattern with a pull pattern: record the owed ETH amount per user and let them call a separate `claimETH()` function. Alternatively, wrap ETH into WETH before transferring to the user, which always succeeds regardless of the recipient's fallback implementation.

### Proof of Concept
1. Deploy a contract `VaultUser` with no `receive` function that holds rsETH.
2. `VaultUser` calls `LRTWithdrawalManager.initiateWithdrawal(ETH_TOKEN, rsETHAmount, "")`. rsETH is transferred to `LRTWithdrawalManager`.
3. Operator calls `unlockQueue(ETH_TOKEN, ...)`. rsETH is burned; ETH is moved from `LRTUnstakingVault` into `LRTWithdrawalManager`.
4. `VaultUser` calls `completeWithdrawal(ETH_TOKEN, "")`. Inside `_processWithdrawalCompletion`, `_transferAsset` executes `payable(VaultUser).call{value: amount}("")`. Because `VaultUser` has no `receive`, the call returns `false`. `EthTransferFailed` is thrown; the transaction reverts.
5. Step 4 can be repeated indefinitely — it always reverts. `VaultUser`'s rsETH is permanently burned and the ETH is permanently locked in `LRTWithdrawalManager`. [1](#0-0) [7](#0-6)

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

**File:** contracts/LRTWithdrawalManager.sol (L192-204)
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
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L300-320)
```text
        // utilized.
        (rsETHBurned, assetAmountUnlocked) = _unlockWithdrawalRequests(
            asset, params.totalAvailableAssets, params.rsETHPrice, params.assetPrice, firstExcludedIndex
        );

        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);

        // If Aave integration is enabled and asset is ETH, deposit to Aave
        if (isAaveIntegrationEnabled && asset == LRTConstants.ETH_TOKEN && assetAmountUnlocked > 0) {
            try this.depositToAaveExternal(assetAmountUnlocked) { }
            catch (bytes memory reason) {
                emit AaveDepositFailed(assetAmountUnlocked, reason);
                // Silently fail if Aave deposit fails (e.g., pool at max capacity)
                // Funds remain in contract for withdrawals
            }
        }

        emit AssetUnlocked(asset, rsETHBurned, assetAmountUnlocked, params.rsETHPrice, params.assetPrice);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L395-414)
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
    }
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
