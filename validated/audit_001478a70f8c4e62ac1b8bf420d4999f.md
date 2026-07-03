Audit Report

## Title
ETH Push-Transfer Revert Permanently Freezes Funds for Contract Recipients — (`contracts/LRTWithdrawalManager.sol`)

## Summary

`_processWithdrawalCompletion` calls `popFront()` and deletes the withdrawal request before calling `_transferAsset`. If the recipient is a contract whose `receive()` reverts, `_transferAsset` throws `EthTransferFailed()`, rolling back all state changes including `popFront()`. Because rsETH is already burned in the prior separate `unlockQueue` transaction, the user permanently loses their rsETH and can never receive the corresponding ETH. Every subsequent completion attempt reverts identically, and the FIFO queue is permanently blocked.

## Finding Description

In `_processWithdrawalCompletion` (lines 699–738), the execution order is:

1. `userAssociatedNonces[asset][user].popFront()` — storage write, line 705
2. `delete withdrawalRequests[requestId]` — storage write, line 712
3. `unlockedWithdrawalsCount[asset]--` — storage write, line 717
4. `_transferAsset(asset, user, request.expectedAssetAmount)` — line 734

`_transferAsset` for ETH (lines 876–883) does:

```solidity
(bool sent,) = payable(to).call{ value: amount }("");
if (!sent) revert EthTransferFailed();
```

If `to` is a contract with a reverting `receive()`, `sent == false` and `EthTransferFailed()` is thrown. This reverts the entire transaction, rolling back all four state changes above — the nonce is restored to the front of the queue.

rsETH is burned in the separate `unlockQueue` transaction (line 305):

```solidity
if (rsETHBurned != 0) IRSETH(lrtConfig.rsETH()).burnFrom(address(this), rsETHBurned);
unstakingVault.redeem(asset, assetAmountUnlocked);
```

By the time `completeWithdrawal` is called, rsETH is already gone. There is no skip-nonce, redirect-recipient, or admin-rescue path anywhere in the contract — confirmed by the absence of any `recover`, `rescue`, `skip`, or `forceWithdraw` function. The NatSpec on `completeWithdrawalForUser` (line 191) incorrectly dismisses this as a gas-grief non-issue for ETH; the user's own `completeWithdrawal` call (line 183) suffers the identical revert. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

## Impact Explanation

- The user's rsETH is permanently burned (in `unlockQueue`), with no corresponding ETH received.
- The ETH is permanently inaccessible to the user — stuck in the withdrawal manager with no rescue path.
- The front nonce is never consumed, permanently blocking all subsequent ETH withdrawal requests for this user (FIFO enforced by `popFront`).
- **Impact: Critical — Permanent freezing of user funds.** [5](#0-4) 

## Likelihood Explanation

Any smart contract address (multisig, DAO treasury, proxy without a `receive` function, or any contract that intentionally rejects ETH) that calls `initiateWithdrawal` for ETH triggers this path. No special permissions are required — `initiateWithdrawal` is open to any address. The condition is deterministic and repeatable: every completion attempt reverts identically. Smart contract wallets are a common and realistic pattern for institutional/DAO users of a liquid restaking protocol. **Likelihood: Medium.** [6](#0-5) 

## Recommendation

Replace the push-payment pattern for ETH with a pull-payment pattern: store the owed ETH amount in a claimable mapping (`pendingETH[user] += amount`) and let the user pull it via a separate `claimETH()` function. Alternatively, wrap ETH as WETH before transferring — `WETH.deposit{value: amount}(); WETH.transfer(to, amount)` never reverts on receipt. At minimum, if the ETH transfer fails, do not revert the entire transaction; instead consume the nonce, emit an event, and store the owed amount for a separate claim.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract RejectETH {
    receive() external payable { revert("no ETH"); }

    function initiateWithdrawal(address wm, address eth, uint256 amt) external {
        IERC20(rsETH).approve(wm, amt);
        ILRTWithdrawalManager(wm).initiateWithdrawal(eth, amt, "");
    }

    function tryComplete(address wm, address eth) external {
        // Always reverts with EthTransferFailed
        ILRTWithdrawalManager(wm).completeWithdrawal(eth, "");
    }
}

// Test sequence (Foundry fork test):
// 1. Deploy RejectETH, fund with rsETH
// 2. RejectETH.initiateWithdrawal(ETH, amount) — succeeds, rsETH locked in manager
// 3. Operator calls unlockQueue(ETH, ...) — rsETH burned, ETH moved to manager
// 4. RejectETH.tryComplete(ETH) — reverts with EthTransferFailed
// 5. Assert: userAssociatedNonces[ETH][RejectETH].front() == original nonce (never consumed)
// 6. Assert: withdrawalRequests[requestId] still exists (delete rolled back)
// 7. Assert: rsETH.balanceOf(manager) == 0 (already burned, unrecoverable)
// 8. Repeat step 4 N times — always reverts, queue permanently blocked
``` [7](#0-6) [8](#0-7)

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

**File:** contracts/LRTWithdrawalManager.sol (L183-184)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
```

**File:** contracts/LRTWithdrawalManager.sol (L191-191)
```text
    /// @dev Not expected to be used for ETH; potential gas grief scenarios are non-impactful for ETH
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

**File:** contracts/utils/DoubleEndedQueue.sol (L97-105)
```text
    function popFront(Uint256Deque storage deque) internal returns (uint256 value) {
        unchecked {
            uint128 frontIndex = deque._begin;
            if (frontIndex == deque._end) revert QueueEmpty();
            value = deque._data[frontIndex];
            delete deque._data[frontIndex];
            deque._begin = frontIndex + 1;
        }
    }
```
