### Title
ETH Withdrawal Permanently Frozen When Recipient Contract Cannot Receive ETH - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager.initiateWithdrawal` imposes no check that the caller is an EOA or a contract capable of receiving ETH. When a smart contract that lacks a `receive`/`fallback` function initiates an ETH withdrawal, the rsETH is burned in a separate `unlockQueue` transaction, but every subsequent attempt to call `completeWithdrawal` reverts because the low-level ETH transfer to the contract fails. The ETH is permanently locked inside `LRTWithdrawalManager` with no recovery path.

### Finding Description
`initiateWithdrawal` accepts any `msg.sender` — including smart contracts — without verifying the caller can receive ETH: [1](#0-0) 

After the request is queued, the operator calls `unlockQueue`, which burns the rsETH held by the withdrawal manager and pulls the corresponding ETH from `LRTUnstakingVault` into `LRTWithdrawalManager`: [2](#0-1) 

When the user later calls `completeWithdrawal`, `_processWithdrawalCompletion` attempts to deliver ETH via: [3](#0-2) 

If `to` is a contract without a `receive` function, `sent` is `false` and the call reverts with `EthTransferFailed`. Because the revert unwinds all state changes in that transaction, the withdrawal request remains in the queue indefinitely. The ETH, however, was already moved into `LRTWithdrawalManager` in the prior `unlockQueue` transaction and cannot leave.

The only administrative escape valve, `sweepRemainingAssets`, is explicitly blocked while any unlocked withdrawal exists: [4](#0-3) 

Because `hasUnlockedWithdrawals` returns `true` for the stuck request, the sweep is permanently gated. The `completeWithdrawalForUser` operator path calls the same `_processWithdrawalCompletion` and fails identically: [5](#0-4) 

### Impact Explanation
After `unlockQueue` executes, the user's rsETH is irreversibly burned and the corresponding ETH is held in `LRTWithdrawalManager`. Every call to `completeWithdrawal` or `completeWithdrawalForUser` reverts. `sweepRemainingAssets` is blocked by the pending unlocked withdrawal. Without a contract upgrade, the ETH is permanently frozen — **Critical: Permanent freezing of funds**.

### Likelihood Explanation
`initiateWithdrawal` is a public, permissionless function callable by any address. Smart contracts that hold rsETH (e.g., vaults, DAOs, multisigs, DeFi integrations) routinely lack a plain ETH `receive` function. No special privilege or exploit is required; the caller simply needs to be a contract that cannot accept ETH. Likelihood is **Medium**.

### Recommendation
Add an EOA-only guard to `initiateWithdrawal` when `asset == LRTConstants.ETH_TOKEN`, mirroring the pattern used elsewhere in the ecosystem:

```solidity
if (asset == LRTConstants.ETH_TOKEN && tx.origin != msg.sender) {
    revert ContractsNotAllowed();
}
```

Alternatively, record a user-supplied `recipient` address at request time and deliver ETH to that address in `_processWithdrawalCompletion`, allowing the initiating contract to specify a payable EOA as the beneficiary.

### Proof of Concept
1. Deploy a contract `Victim` that holds rsETH but has no `receive()` function.
2. `Victim` approves `LRTWithdrawalManager` and calls `initiateWithdrawal(ETH_TOKEN, amount, "")`. rsETH is transferred to the manager.
3. Operator calls `unlockQueue(ETH_TOKEN, ...)`. rsETH is burned; ETH is moved from `LRTUnstakingVault` into `LRTWithdrawalManager`.
4. `Victim` calls `completeWithdrawal(ETH_TOKEN, "")`. `_transferAsset` executes `payable(Victim).call{value: amount}("")` → returns `false` → reverts with `EthTransferFailed`.
5. Step 4 can be repeated indefinitely; it always reverts.
6. `sweepRemainingAssets(ETH_TOKEN)` reverts with `PendingWithdrawalsExist`.
7. The ETH is permanently locked in `LRTWithdrawalManager`; the rsETH is permanently burned.

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
