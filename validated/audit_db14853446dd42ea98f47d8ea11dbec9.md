### Title
`setWithdrawalDelayBlocks` retroactively applies new delay to in-flight withdrawal requests, causing temporary fund freeze - (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager.setWithdrawalDelayBlocks()` allows the LRT manager to change the global `withdrawalDelayBlocks` parameter at any time. Unlike the FrankenDAO pattern where a bonus was stored per-user at stake time, the withdrawal delay is **never stored per request**. Both the operator-facing `_unlockWithdrawalRequests()` and the user-facing `_processWithdrawalCompletion()` read the **current** `withdrawalDelayBlocks` value when evaluating whether a request has waited long enough. Any increase to this parameter retroactively extends the lock period for all existing, in-flight withdrawal requests — requests where the user has already surrendered their rsETH to the contract.

---

### Finding Description

When a user calls `initiateWithdrawal()`, their rsETH is transferred to the contract and a `WithdrawalRequest` struct is stored containing only `rsETHUnstaked`, `expectedAssetAmount`, and `withdrawalStartBlock`. [1](#0-0) 

The delay parameter itself is **not captured** in the struct. Both downstream enforcement points read the live global value:

In `_unlockWithdrawalRequests()` (operator path): [2](#0-1) 

In `_processWithdrawalCompletion()` (user path): [3](#0-2) 

The manager can raise `withdrawalDelayBlocks` up to the 16-day ceiling at any time: [4](#0-3) 

The default is `8 days / 12 seconds`. A manager increase to `16 days / 12 seconds` doubles the lock period for every request already in the queue.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

Users who called `initiateWithdrawal()` have already transferred their rsETH to the contract and committed to the withdrawal. They cannot cancel and reclaim their rsETH. If `withdrawalDelayBlocks` is raised after their request is submitted, their funds are locked for longer than the delay that was in effect when they initiated — up to 16 days instead of the expected 8 days. The freeze is temporary (it resolves once the new delay elapses), but the user has no recourse during that period.

---

### Likelihood Explanation

**Medium.** The `onlyLRTManager` role is a privileged but operationally active role used for routine protocol management. A delay increase could occur legitimately (e.g., in response to a security incident) or accidentally. No governance timelock is required for this call. Any time the delay is raised while withdrawal requests are pending, all queued users are affected simultaneously.

---

### Recommendation

Snapshot `withdrawalDelayBlocks` into each `WithdrawalRequest` struct at the time `initiateWithdrawal()` is called, and use the per-request snapshot in both `_unlockWithdrawalRequests()` and `_processWithdrawalCompletion()` instead of the live global value. This ensures that a user's lock period is determined by the rules in effect when they committed their rsETH, not by any subsequent parameter change.

```solidity
struct WithdrawalRequest {
    uint256 rsETHUnstaked;
    uint256 expectedAssetAmount;
    uint256 withdrawalStartBlock;
    uint256 withdrawalDelayBlocksSnapshot; // add this
}
```

Then enforce:
```solidity
if (block.number < request.withdrawalStartBlock + request.withdrawalDelayBlocksSnapshot)
    revert WithdrawalDelayNotPassed();
```

---

### Proof of Concept

1. Protocol initializes with `withdrawalDelayBlocks = 8 days / 12 seconds` (~57,600 blocks).
2. Alice calls `initiateWithdrawal(ETH, 1e18, "")`. Her rsETH is transferred to the contract. Her `WithdrawalRequest` stores `withdrawalStartBlock = N`.
3. At block `N + 50,000` (still within the 8-day window), the manager calls `setWithdrawalDelayBlocks(16 days / 12 seconds)` (~115,200 blocks).
4. At block `N + 57,601` (past the original 8-day delay), Alice calls `completeWithdrawal()`. The check `block.number < N + 115,200` is true → `WithdrawalDelayNotPassed` revert.
5. Alice's rsETH remains locked in the contract for an additional ~8 days beyond her original expectation, with no ability to cancel. [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L162-178)
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

        emit ReferralIdEmitted(referralId);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L338-344)
```text
    function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
        // Set an upper limit of no more than 16 days
        if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();

        withdrawalDelayBlocks = withdrawalDelayBlocks_;
        emit WithdrawalDelayBlocksUpdated(withdrawalDelayBlocks);
    }
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
