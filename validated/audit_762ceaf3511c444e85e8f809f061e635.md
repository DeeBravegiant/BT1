Audit Report

## Title
ETH Permanently Frozen When Withdrawal Initiator Is a Contract Without `receive()` - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`initiateWithdrawal` accepts any `msg.sender` including smart contracts that cannot receive ETH. After `unlockQueue` irreversibly burns the user's rsETH and moves the corresponding ETH into `LRTWithdrawalManager`, every subsequent call to `completeWithdrawal` or `completeWithdrawalForUser` reverts because the low-level ETH transfer to the non-payable contract fails. The ETH is permanently locked with no in-contract recovery path.

## Finding Description
`initiateWithdrawal` imposes no restriction on the caller's ability to receive ETH: [1](#0-0) 

When the operator later calls `unlockQueue`, rsETH held by the manager is burned and the corresponding ETH is pulled from `LRTUnstakingVault` into `LRTWithdrawalManager` — both irreversible state changes in a separate transaction: [2](#0-1) 

When the user calls `completeWithdrawal`, `_processWithdrawalCompletion` decrements `unlockedWithdrawalsCount` and deletes the request *before* calling `_transferAsset`. If `_transferAsset` reverts, the entire transaction unwinds, restoring the request and the count: [3](#0-2) 

`_transferAsset` for ETH uses a low-level call and reverts on failure: [4](#0-3) 

Because `unlockedWithdrawalsCount[asset]` is never decremented (the decrement always reverts), `hasUnlockedWithdrawals` permanently returns `true`, blocking the only administrative escape valve: [5](#0-4) 

The operator path `completeWithdrawalForUser` calls the same `_processWithdrawalCompletion` and fails identically: [6](#0-5) 

There is no cancel-withdrawal, force-complete, or per-request admin override function in the contract. The ETH cannot leave without a proxy upgrade.

## Impact Explanation
After `unlockQueue` executes, the user's rsETH is irreversibly burned and the ETH is held in `LRTWithdrawalManager`. Every call to `completeWithdrawal` or `completeWithdrawalForUser` reverts. `sweepRemainingAssets` is permanently gated by the stuck unlocked withdrawal. This constitutes **Critical: Permanent freezing of funds** — the ETH is unrecoverable without a contract upgrade.

## Likelihood Explanation
`initiateWithdrawal` is a public, permissionless function. Smart contracts holding rsETH — DeFi vaults, DAOs, multisigs, yield aggregators — routinely lack a plain ETH `receive` function. No special privilege or exploit is required; the caller simply needs to be a contract that cannot accept ETH. Likelihood is **Medium**, making the overall severity **Critical**.

## Recommendation
Add an EOA-only guard to `initiateWithdrawal` when `asset == LRTConstants.ETH_TOKEN`:

```solidity
if (asset == LRTConstants.ETH_TOKEN && tx.origin != msg.sender) {
    revert ContractsNotAllowed();
}
```

Alternatively, record a user-supplied `recipient` address at request time and deliver ETH to that address in `_processWithdrawalCompletion`, allowing the initiating contract to specify a payable EOA as the beneficiary.

## Proof of Concept
1. Deploy `Victim` — a contract holding rsETH with no `receive()` function.
2. `Victim` approves `LRTWithdrawalManager` and calls `initiateWithdrawal(ETH_TOKEN, amount, "")`. rsETH is transferred to the manager.
3. Operator calls `unlockQueue(ETH_TOKEN, ...)`. rsETH is burned; ETH is moved from `LRTUnstakingVault` into `LRTWithdrawalManager`.
4. `Victim` calls `completeWithdrawal(ETH_TOKEN, "")`. `_transferAsset` executes `payable(Victim).call{value: amount}("")` → returns `false` → reverts with `EthTransferFailed`. All state changes in this tx revert.
5. Step 4 can be repeated indefinitely; it always reverts.
6. `sweepRemainingAssets(ETH_TOKEN)` reverts with `PendingWithdrawalsExist` because `unlockedWithdrawalsCount[ETH_TOKEN] > 0`.
7. The ETH is permanently locked in `LRTWithdrawalManager`; the rsETH is permanently burned.

**Foundry test plan**: Deploy a mock `Victim` contract without `receive()`, mock the oracle and vault, call the sequence above, and assert that `address(LRTWithdrawalManager).balance` equals the locked amount after step 3 and remains unchanged after repeated step-4 calls, while `hasUnlockedWithdrawals(ETH_TOKEN)` returns `true` throughout.

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

**File:** contracts/LRTWithdrawalManager.sol (L712-734)
```text
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
