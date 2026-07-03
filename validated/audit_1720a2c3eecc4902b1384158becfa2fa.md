### Title
Withdrawal May Revert Due to stETH 1-Wei Rounding, Permanently Freezing User Funds - (File: contracts/LRTWithdrawalManager.sol)

### Summary
The `unlockQueue` function in `LRTWithdrawalManager` burns rsETH and redeems a computed `assetAmountToUnlock` of stETH from `LRTUnstakingVault` into the withdrawal manager. Because stETH uses shares-based accounting, its `transfer` rounds down by 1 wei, so the withdrawal manager receives `assetAmountToUnlock - 1` stETH. When the last user in the unlocked batch calls `completeWithdrawal`, the `safeTransfer` of their `payoutAmount` reverts because the contract holds 1 fewer token than required. Since the rsETH burn occurred in the prior `unlockQueue` transaction, it is not rolled back, permanently destroying the user's rsETH while their stETH equivalent remains frozen in the contract.

### Finding Description
The two-step withdrawal lifecycle is:

**Step 1 — `unlockQueue`** (operator-triggered, separate transaction): [1](#0-0) 

`rsETHBurned` is burned from the withdrawal manager's balance (previously deposited by users during `initiateWithdrawal`), and then `unstakingVault.redeem(asset, assetAmountToUnlock)` is called.

**`LRTUnstakingVault.redeem`** for a non-ETH asset executes: [2](#0-1) 

This calls `IERC20(stETH).safeTransfer(withdrawalManager, assetAmountToUnlock)`. stETH's `transfer` internally converts the token amount to shares via `getSharesByPooledEth(amount) = amount * totalShares / totalPooledEth` (integer division, rounds down), then converts back. The withdrawal manager therefore receives `assetAmountToUnlock - 1` stETH while the vault's balance decreases by the full `assetAmountToUnlock`. The 1-wei discrepancy is silently absorbed.

**Step 2 — `completeWithdrawal`** (user-triggered, separate transaction): [3](#0-2) 

`_processWithdrawalCompletion` calls `_transferAsset(stETH, user, request.expectedAssetAmount)`: [4](#0-3) 

If N users were unlocked in one `unlockQueue` call with payouts P1…PN summing to T, the withdrawal manager holds T−1 stETH. Users 1 through N−1 successfully withdraw their shares. When user N calls `completeWithdrawal`, the manager holds only PN−1 stETH, so `safeTransfer(user, PN)` reverts. This revert does **not** roll back the rsETH burn from the prior `unlockQueue` transaction.

The `getExpectedAssetAmount` formula used to compute payouts performs plain integer division: [5](#0-4) 

No overshoot or rounding buffer is applied anywhere in the redemption path.

### Impact Explanation
The last user in any `unlockQueue` batch for stETH has their rsETH permanently burned (it was destroyed in the prior transaction) while their stETH equivalent is frozen inside `LRTWithdrawalManager`. The `sweepRemainingAssets` function cannot recover it because `unlockedWithdrawalsCount[stETH] > 0` (the failed `completeWithdrawal` reverts before decrementing the counter): [6](#0-5) 

The user cannot retry successfully because the withdrawal manager's stETH balance remains 1 wei short of `payoutAmount` until an external source (stETH rebasing or admin top-up) covers the deficit. This constitutes a permanent freeze of user funds from the user's perspective, matching the **Critical** impact tier.

### Likelihood Explanation
stETH's 1-wei rounding on `transfer` is a well-documented, deterministic behavior that occurs whenever `amount * totalShares % totalPooledEth ≠ 0`. Given that `totalShares` and `totalPooledEth` are large, non-round numbers that change with every block, this condition is satisfied for the vast majority of transfer amounts. Any `unlockQueue` call that processes more than one withdrawal request for stETH will expose the last user to this failure. Likelihood is **High**.

### Recommendation
1. **Overshoot the redemption amount**: When calling `unstakingVault.redeem(asset, assetAmountToUnlock)`, add a small buffer (e.g., 2 wei) to the redeemed amount so the withdrawal manager always holds at least as much as the sum of all pending payouts.
2. **Use `min(request.expectedAssetAmount, actualBalance)` at transfer time**: In `_processWithdrawalCompletion`, cap the transfer amount to the contract's actual balance for rebasing tokens, absorbing any 1-wei deficit gracefully.
3. **Alternatively, track actual received amounts**: After `unstakingVault.redeem`, measure the actual balance increase and distribute that amount proportionally rather than using the pre-computed `assetAmountToUnlock`.

### Proof of Concept
1. Alice and Bob each call `initiateWithdrawal(stETH, 1e18, "")`. The withdrawal manager holds 2e18 rsETH.
2. Operator calls `unlockQueue(stETH, ...)`. `_unlockWithdrawalRequests` sets Alice's payout = 1.0005e18 stETH, Bob's payout = 1.0005e18 stETH; `assetAmountToUnlock = 2.001e18`.
3. `IRSETH.burnFrom(withdrawalManager, 2e18)` — both users' rsETH permanently destroyed.
4. `unstakingVault.redeem(stETH, 2.001e18)` — due to stETH rounding, withdrawal manager receives `2.001e18 - 1` stETH.
5. Alice calls `completeWithdrawal(stETH, "")` — receives 1.0005e18 stETH. Withdrawal manager now holds `1.0005e18 - 1` stETH.
6. Bob calls `completeWithdrawal(stETH, "")` — `safeTransfer(Bob, 1.0005e18)` reverts: balance is `1.0005e18 - 1`.
7. Bob's rsETH is already burned. Bob's stETH is frozen in the withdrawal manager. Bob has permanently lost value with no recourse.

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L580-594)
```text
    function getExpectedAssetAmount(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 underlyingToReceive)
    {
        // setup oracle contract
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
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
