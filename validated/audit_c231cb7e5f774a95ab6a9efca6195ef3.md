Audit Report

## Title
ETH Withdrawal Permanently Frozen for Contracts Unable to Receive ETH — (`contracts/LRTWithdrawalManager.sol`)

## Summary

`LRTWithdrawalManager._transferAsset` pushes ETH directly to the requesting user's address with no alternative delivery path. If the user is a smart contract that cannot receive ETH, every `completeWithdrawal` call reverts. Because the rsETH burn occurs in the prior `unlockQueue` transaction (already finalized), the ETH owed to that user is permanently locked in `LRTWithdrawalManager` with no on-chain recovery path.

## Finding Description

The withdrawal lifecycle spans two separate transactions:

**Transaction 1 — `unlockQueue`** (operator-only): Burns rsETH held by the contract and redeems ETH from the unstaking vault into `LRTWithdrawalManager`. [1](#0-0) 

**Transaction 2 — `completeWithdrawal`**: The user calls this to receive their ETH. It delegates to `_processWithdrawalCompletion`, which calls `_transferAsset(asset, user, request.expectedAssetAmount)`. [2](#0-1) 

`_transferAsset` for ETH: [3](#0-2) 

If `to` is a contract that cannot receive ETH, `sent == false` and the function reverts with `EthTransferFailed()`. Because this revert unwinds all state changes within that call (the `popFront`, `delete withdrawalRequests`, and `unlockedWithdrawalsCount--` are all reverted), the withdrawal request record is restored and the user can retry. However, the rsETH burned in `unlockQueue` was in a prior, already-finalized transaction and is **not** restored.

There is no `completeWithdrawalTo(address recipient)` or any other function allowing the user to redirect ETH to a different address. The operator variant `completeWithdrawalForUser` also hardcodes `user` as the destination: [4](#0-3) 

The `sweepRemainingAssets` function cannot rescue the funds either, because it requires `hasUnlockedWithdrawals(asset) == false`, but the stuck user's request keeps `unlockedWithdrawalsCount[asset] > 0` indefinitely. [5](#0-4) 

## Impact Explanation

After `unlockQueue` burns the user's rsETH, the ETH owed to that user sits in `LRTWithdrawalManager`. If the user's address cannot receive ETH, every subsequent `completeWithdrawal` call reverts. The user has permanently lost their rsETH and cannot recover the corresponding ETH. No on-chain recovery path exists short of a contract upgrade.

**Impact: Critical — Permanent freezing of funds.**

## Likelihood Explanation

Smart contract wallets (Gnosis Safe multisigs, account-abstraction wallets, protocol treasuries) are common participants in DeFi withdrawal flows. A multisig with non-trivial receive logic, or a contract with no `receive()` function at all, will fail every ETH push. The developer comment on `completeWithdrawalForUser` even acknowledges this class of issue for ETH: [6](#0-5) 

Any such user who initiates an ETH withdrawal and whose request is subsequently unlocked by the operator is permanently affected.

**Likelihood: Medium** — requires the withdrawing address to be a contract that cannot receive ETH, which is realistic for multisigs and protocol-owned addresses.

## Recommendation

Add a `completeWithdrawalTo(address asset, address payable recipient, string calldata referralId)` function that allows `msg.sender` to redirect their ETH to an arbitrary `recipient` address. Only the original requester (`msg.sender`) should be able to choose the redirect target, preserving fund attribution:

```solidity
function completeWithdrawalTo(
    address asset,
    address payable recipient,
    string calldata referralId
) external nonReentrant whenNotPaused {
    _processWithdrawalCompletionTo(asset, msg.sender, recipient, referralId);
}
```

Where `_processWithdrawalCompletionTo` is a variant of `_processWithdrawalCompletion` that calls `_transferAsset(asset, recipient, amount)` instead of `_transferAsset(asset, user, amount)`.

## Proof of Concept

1. A Gnosis Safe multisig (`userSafe`) holds rsETH and calls `initiateWithdrawal(ETH_TOKEN, rsETHAmount)`. rsETH is transferred to `LRTWithdrawalManager`. [7](#0-6) 

2. The operator calls `unlockQueue(ETH_TOKEN, ...)`. rsETH is burned from the contract and ETH is redeemed into `LRTWithdrawalManager`. [1](#0-0) 

3. `userSafe` calls `completeWithdrawal(ETH_TOKEN, "")`. Inside `_processWithdrawalCompletion`, `_transferAsset(ETH_TOKEN, userSafe, amount)` executes `payable(userSafe).call{value: amount}("")`. The Safe's fallback reverts (e.g., requires a specific payload or has no `receive()` function). `sent == false`, so `EthTransferFailed()` is thrown. [8](#0-7) 

4. The transaction reverts. The withdrawal request record is restored. The rsETH burn from Step 2 is permanent. `userSafe` retries indefinitely — every attempt reverts. ETH is permanently locked in `LRTWithdrawalManager`.

**Foundry test plan**: Deploy a mock contract with no `receive()` function as `userSafe`. Have it call `initiateWithdrawal`. Simulate `unlockQueue` as operator. Assert that `completeWithdrawal` always reverts with `EthTransferFailed()`, that the rsETH balance of `userSafe` is zero, and that the ETH balance of `LRTWithdrawalManager` equals the owed amount indefinitely.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L191-191)
```text
    /// @dev Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH
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

**File:** contracts/LRTWithdrawalManager.sol (L305-307)
```text
        if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
        //Take the amount to distribute from vault
        unstakingVault.redeem(asset, assetAmountUnlocked);
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

**File:** contracts/LRTWithdrawalManager.sol (L734-734)
```text
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
