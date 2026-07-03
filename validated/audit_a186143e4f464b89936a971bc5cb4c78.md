### Title
Instant `withdrawalDelayBlocks` Update Retroactively Freezes Pending Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`setWithdrawalDelayBlocks` takes effect immediately and applies retroactively to all already-initiated withdrawal requests. Because `withdrawalDelayBlocks` is read dynamically at completion time rather than being captured at initiation time, a manager can increase the delay up to 16 days and instantly extend the lock period for every user who already submitted a withdrawal, temporarily freezing their funds beyond the delay they accepted when they initiated.

### Finding Description
When a user calls `initiateWithdrawal`, only `withdrawalStartBlock = block.number` is stored in the `WithdrawalRequest` struct:

```solidity
// LRTWithdrawalManager.sol line 751-753
withdrawalRequests[requestId] = WithdrawalRequest({
    rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
});
```

The delay itself is **not** snapshotted. Both `_processWithdrawalCompletion` (user-facing `completeWithdrawal`) and `_unlockWithdrawalRequests` (operator-facing `unlockQueue`) read the global `withdrawalDelayBlocks` at execution time:

```solidity
// LRTWithdrawalManager.sol line 715
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();

// LRTWithdrawalManager.sol line 795
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
```

`setWithdrawalDelayBlocks` is callable by any account holding `onlyLRTManager` and takes effect in the same block with no delay:

```solidity
// LRTWithdrawalManager.sol line 338-343
function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
    if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();
    withdrawalDelayBlocks = withdrawalDelayBlocks_;
    emit WithdrawalDelayBlocksUpdated(withdrawalDelayBlocks);
}
```

The default delay is `8 days / 12 seconds` (~57,600 blocks). The manager can raise it to `16 days / 12 seconds` (~115,200 blocks) in a single transaction. Every pending withdrawal that was initiated under the 8-day assumption is immediately subject to the new 16-day delay, with no recourse for the user.

### Impact Explanation
Users who called `initiateWithdrawal` and burned their rsETH (which is transferred to the contract at initiation) are now locked for up to 8 additional days beyond what they agreed to. Their rsETH is already held by the contract and cannot be reclaimed. This constitutes a **temporary freezing of funds** for all users with pending withdrawal requests at the time of the parameter change.

### Likelihood Explanation
The `onlyLRTManager` role is an operational role used for routine parameter updates. A legitimate operational decision to increase the delay (e.g., during a market stress event or security incident) would immediately and silently harm all users with in-flight withdrawals. No malicious intent is required; the design flaw is that the parameter change has no grace period for existing requests. The protocol's own NatSpec on the related `instantWithdrawal` function (line 210–211) already acknowledges the analogous instant-effect problem for fees, confirming the pattern is a known design gap.

### Recommendation
Snapshot `withdrawalDelayBlocks` into each `WithdrawalRequest` at initiation time, so that a user's lock period is determined by the delay in effect when they submitted their request, not when they (or the operator) attempt to complete it:

```solidity
struct WithdrawalRequest {
    uint256 rsETHUnstaked;
    uint256 expectedAssetAmount;
    uint256 withdrawalStartBlock;
    uint256 withdrawalDelayBlocksSnapshot; // add this
}
```

Alternatively, implement a two-step timelock for `setWithdrawalDelayBlocks` (signal intent, then apply after a delay) so users have time to react before the new delay takes effect.

### Proof of Concept

1. Alice calls `initiateWithdrawal(ETH, 10e18, "")` at block N. The contract records `withdrawalStartBlock = N`. The current `withdrawalDelayBlocks` is 57,600 (8 days). Alice expects to call `completeWithdrawal` at block N + 57,600.

2. At block N + 50,000 (still 7,600 blocks before Alice's expected unlock), the LRT manager calls `setWithdrawalDelayBlocks(115200)` (16 days). This takes effect immediately.

3. Alice attempts `completeWithdrawal` at block N + 57,600. The check at line 715 evaluates: `block.number (N+57,600) < N + 115,200` → `true` → `revert WithdrawalDelayNotPassed()`.

4. Alice's ETH is frozen for an additional ~57,600 blocks (~8 days) beyond what she accepted at initiation. She has no way to cancel the withdrawal and recover her rsETH. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L208-211)
```text
    /// @param rsETHUnstaked The amount of rsETH tokens to burn for withdrawal
    /// @param referralId The referral identifier for tracking
    /// @dev Uses the fee set at execution time. Managers can raise it right before this call, making withdrawals cost
    /// more than expected.
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

**File:** contracts/LRTWithdrawalManager.sol (L713-715)
```text

        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
```

**File:** contracts/LRTWithdrawalManager.sol (L750-753)
```text
        // Create and store the new withdrawal request.
        withdrawalRequests[requestId] = WithdrawalRequest({
            rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
        });
```

**File:** contracts/LRTWithdrawalManager.sol (L793-795)
```text

            // Check that the withdrawal delay has passed since the request's initiation.
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
```
