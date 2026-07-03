### Title
`withdrawalDelayBlocks` Read at Completion Time Instead of Snapshotted at Initiation — Retroactive Delay Extension Temporarily Freezes In-Flight Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

### Summary

`LRTWithdrawalManager` uses a global `withdrawalDelayBlocks` parameter that can be updated by the manager at any time. This value is read at withdrawal completion/unlock time rather than being snapshotted when the user initiates the withdrawal. A legitimate manager update to a higher delay value retroactively extends the lock period for all pending withdrawal requests, temporarily freezing user funds beyond the delay they accepted at initiation.

### Finding Description

When a user calls `initiateWithdrawal()`, the `WithdrawalRequest` struct stores only `withdrawalStartBlock` — the block at which the request was created. The `withdrawalDelayBlocks` value is **not** captured at that point. [1](#0-0) 

Later, both `_processWithdrawalCompletion` (called by `completeWithdrawal`) and `_unlockWithdrawalRequests` (called by `unlockQueue`) read the **current** global `withdrawalDelayBlocks` to enforce the delay: [2](#0-1) [3](#0-2) 

The manager can update `withdrawalDelayBlocks` at any time up to the 16-day ceiling with no timelock: [4](#0-3) 

This is structurally identical to the Bancor bug: a mutable configuration parameter is consumed at execution time rather than being locked at the start of the user's operation.

### Impact Explanation

**Medium — Temporary freezing of funds.**

A user who initiated a withdrawal under an 8-day delay can find their funds locked for up to 16 days if the manager increases `withdrawalDelayBlocks` after initiation. The two-step impact path:

1. `_unlockWithdrawalRequests` skips requests whose `withdrawalStartBlock + newDelay` has not yet passed, so the operator's `unlockQueue` call cannot unlock them.
2. Even for already-unlocked requests, `_processWithdrawalCompletion` re-checks the delay at line 715 against the **current** `withdrawalDelayBlocks`, so `completeWithdrawal` reverts with `WithdrawalDelayNotPassed` until the new (longer) delay elapses. [5](#0-4) 

### Likelihood Explanation

The manager role is a normal operational role that is expected to tune protocol parameters. Increasing the withdrawal delay is a plausible governance action (e.g., in response to a security incident or EigenLayer upgrade). There is no timelock, no notice period, and no on-chain mechanism preventing the change from taking immediate retroactive effect on all pending requests. The impact is automatic and requires no further action from the manager after the parameter update.

### Recommendation

Snapshot `withdrawalDelayBlocks` at the time `initiateWithdrawal` is called and store it inside the `WithdrawalRequest` struct. Both `_processWithdrawalCompletion` and `_unlockWithdrawalRequests` should then compare against `request.withdrawalDelayBlocks` rather than the global variable. This mirrors the Bancor fix of storing the slippage value with the batch.

```solidity
struct WithdrawalRequest {
    uint256 rsETHUnstaked;
    uint256 expectedAssetAmount;
    uint256 withdrawalStartBlock;
    uint256 withdrawalDelayBlocks; // snapshotted at initiation
}
``` [6](#0-5) 

### Proof of Concept

1. Manager sets `withdrawalDelayBlocks = 57,600` (~8 days at 12 s/block).
2. Alice calls `initiateWithdrawal(stETH, 1e18, "")` at block `B`. Her request stores `withdrawalStartBlock = B`.
3. At block `B + 57,600` the delay has elapsed. Alice expects to be able to complete her withdrawal.
4. Manager calls `setWithdrawalDelayBlocks(115_200)` (16 days), a legitimate governance action.
5. Operator calls `unlockQueue(stETH, ...)`. Inside `_unlockWithdrawalRequests`, the check `block.number < B + 115_200` is true, so Alice's request is **skipped** and remains locked.
6. Even if the operator had already unlocked Alice's request before step 4, Alice's call to `completeWithdrawal` now hits `block.number < B + 115_200` at line 715 and reverts with `WithdrawalDelayNotPassed`.
7. Alice's rsETH is held by the contract and her underlying asset is inaccessible for an additional ~8 days beyond what she agreed to at initiation. [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L162-175)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);

        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);

        if (expectedAssetAmount > getAvailableAssetAmount(asset)) revert ExceedAmountToWithdraw();

        // preventing over-withdrawal.
        assetsCommitted[asset] += expectedAssetAmount;

        _addUserWithdrawalRequest(asset, rsETHUnstaked, expectedAssetAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L338-343)
```text
    function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
        // Set an upper limit of no more than 16 days
        if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();

        withdrawalDelayBlocks = withdrawalDelayBlocks_;
        emit WithdrawalDelayBlocksUpdated(withdrawalDelayBlocks);
```

**File:** contracts/LRTWithdrawalManager.sol (L370-375)
```text
    /// @param feeBasisPoints The fee in basis points (1 = 0.01%)
    /// @dev Only callable by LRTManager
    function setInstantWithdrawalFee(uint256 feeBasisPoints) external onlyLRTManager {
        if (feeBasisPoints > 1000) revert FeeTooHigh(); // Max 10%
        instantWithdrawalFee = feeBasisPoints;
        emit InstantWithdrawalFeeUpdated(feeBasisPoints);
```

**File:** contracts/LRTWithdrawalManager.sol (L699-715)
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
```

**File:** contracts/LRTWithdrawalManager.sol (L751-753)
```text
        withdrawalRequests[requestId] = WithdrawalRequest({
            rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
        });
```

**File:** contracts/LRTWithdrawalManager.sol (L794-795)
```text
            // Check that the withdrawal delay has passed since the request's initiation.
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
```

**File:** contracts/interfaces/ILRTWithdrawalManager.sol (L39-43)
```text
    struct WithdrawalRequest {
        uint256 rsETHUnstaked;
        uint256 expectedAssetAmount;
        uint256 withdrawalStartBlock;
    }
```
