Audit Report

## Title
Permanent Freezing of ETH for Contract Recipients Unable to Receive ETH — (`contracts/LRTWithdrawalManager.sol`)

## Summary
When a contract address that cannot receive ETH (no `receive()` or a reverting one) initiates an ETH withdrawal, the `_transferAsset` call reverts with `EthTransferFailed`, rolling back all state mutations. Neither `completeWithdrawal` nor `completeWithdrawalForUser` can redirect ETH to an alternative address, and `sweepRemainingAssets` is permanently blocked by the non-zero `unlockedWithdrawalsCount`. The ETH is irrecoverably frozen in `LRTWithdrawalManager` without a contract upgrade.

## Finding Description
`_processWithdrawalCompletion` performs state mutations — `popFront` on `userAssociatedNonces` (L705), `delete withdrawalRequests[requestId]` (L712), and `unlockedWithdrawalsCount[asset]--` (L717) — before calling `_transferAsset(asset, user, request.expectedAssetAmount)` at L734. [1](#0-0) 

`_transferAsset` for ETH uses a low-level call; if `to` is a contract that reverts on ETH receipt, `sent == false` and `EthTransferFailed` is thrown. Solidity reverts all state changes, leaving the request intact and `unlockedWithdrawalsCount[asset] >= 1`.

`completeWithdrawalForUser` provides no alternative recipient — it passes `user` (the reverting address) directly into `_processWithdrawalCompletion`: [2](#0-1) 

The operator recovery path described in the report does not exist. `sweepRemainingAssets` is also blocked: [3](#0-2) 

Because the stuck withdrawal keeps `unlockedWithdrawalsCount[asset] > 0`, `hasUnlockedWithdrawals(asset)` remains `true` and `sweepRemainingAssets` always reverts with `PendingWithdrawalsExist`. There is no on-chain path to recover the ETH.

## Impact Explanation
**Critical — Permanent Freezing of Funds.** ETH redeemed from the unstaking vault into `LRTWithdrawalManager` has no on-chain recovery path. The funds are permanently locked until a contract upgrade is deployed. This matches the allowed Critical impact class of "Permanent freezing of funds."

## Likelihood Explanation
Moderate. Any smart contract that initiates an ETH withdrawal but lacks a payable `receive()` — including custom treasury contracts, contracts with conditional/guarded `receive()` functions, or contracts with reentrancy locks on `receive()` — will trigger this. Protocol integrators and on-chain treasury addresses are realistic users of this withdrawal flow.

## Recommendation
Add an `alternativeRecipient` parameter to `completeWithdrawalForUser` so operators can redirect ETH to a working address:

```solidity
function completeWithdrawalForUser(
    address asset,
    address user,
    address recipient,   // where to actually send the funds
    string calldata referralId
) external nonReentrant whenNotPaused onlyLRTOperator {
    _processWithdrawalCompletion(asset, user, recipient, referralId);
    emit AssetWithdrawalCompletedBy(msg.sender);
}
```

Pass `recipient` through `_processWithdrawalCompletion` to `_transferAsset` instead of `user`. For `completeWithdrawal`, default `recipient = msg.sender`. This preserves the self-service path while giving operators a recovery mechanism for stuck ETH withdrawals.

## Proof of Concept
1. Deploy a contract `RevertOnReceive` with no `receive()` function.
2. Fund it with rsETH; call `initiateWithdrawal(ETH_TOKEN, rsETHAmount, "")` from it.
3. Operator calls `unlockQueue` for `ETH_TOKEN`.
4. `vm.expectRevert(EthTransferFailed); revertContract.tryComplete(wm);` — reverts as expected.
5. Operator calls `completeWithdrawalForUser(ETH_TOKEN, address(revertContract), "")` — also reverts with `EthTransferFailed`.
6. Assert `unlockedWithdrawalsCount[ETH_TOKEN]` is unchanged (still `> 0`).
7. Assert `sweepRemainingAssets(ETH_TOKEN)` reverts with `PendingWithdrawalsExist`.
8. Confirm ETH balance of `LRTWithdrawalManager` is unchanged — funds are permanently locked. [4](#0-3)

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L403-403)
```text
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

**File:** contracts/LRTWithdrawalManager.sol (L876-880)
```text
    function _transferAsset(address asset, address to, uint256 amount) internal {
        if (asset == LRTConstants.ETH_TOKEN) {
            (bool sent,) = payable(to).call{ value: amount }("");
            if (!sent) revert EthTransferFailed();
        } else {
```
