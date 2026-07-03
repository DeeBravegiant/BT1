### Title
`withdrawalDelayBlocks` Not Snapshotted at Request Initiation Causes Retroactive Delay Extension for Pending Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager` stores only `withdrawalStartBlock` in each `WithdrawalRequest` struct, but reads the global `withdrawalDelayBlocks` at completion time. Because `setWithdrawalDelayBlocks` can be called at any time by the manager, any increase to the delay retroactively extends the lock period for all already-queued withdrawal requests, temporarily freezing user funds beyond the delay they accepted at initiation.

---

### Finding Description

When a user calls `initiateWithdrawal`, the contract records the current block number as `withdrawalStartBlock` but does **not** snapshot the current `withdrawalDelayBlocks` value into the request struct.

`_addUserWithdrawalRequest` stores:

```solidity
withdrawalRequests[requestId] = WithdrawalRequest({
    rsETHUnstaked: rsETHUnstaked,
    expectedAssetAmount: expectedAssetAmount,
    withdrawalStartBlock: block.number   // ← only block number, no delay snapshot
});
``` [1](#0-0) 

At completion time, `_processWithdrawalCompletion` enforces the delay using the **current global** `withdrawalDelayBlocks`:

```solidity
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
``` [2](#0-1) 

The manager can update this global value at any time, up to a ceiling of 16 days:

```solidity
function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
    if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();
    withdrawalDelayBlocks = withdrawalDelayBlocks_;
    ...
}
``` [3](#0-2) 

The default delay is `8 days / 12 seconds` (set at `initialize`). If the manager raises it to the maximum of `16 days / 12 seconds` after users have already queued withdrawals, every pending request is retroactively subject to the new 16-day window, even though those users locked their rsETH under the expectation of an 8-day wait. [4](#0-3) 

This is structurally identical to the Bancor finding: a mutable global parameter (`maximumSlippage` / `withdrawalDelayBlocks`) is read at processing time rather than being captured at the moment the user's request is submitted.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

Users who have already called `initiateWithdrawal` and transferred their rsETH to the contract cannot complete their withdrawal until the new, longer delay elapses. Their rsETH is held by the contract and cannot be reclaimed during this period. The maximum retroactive extension is from 8 days to 16 days (an additional ~8 days of lock-up), bounded by the on-chain ceiling.

---

### Likelihood Explanation

The manager role is a live operational key used for routine parameter updates. A legitimate security-motivated increase to the withdrawal delay (e.g., in response to an exploit or oracle anomaly) would inadvertently affect all pending requests without any malicious intent. The scenario requires no attacker — only a routine admin action applied at the wrong time. Given that the protocol actively uses `setWithdrawalDelayBlocks` as an operational lever, the probability of this occurring is realistic.

---

### Recommendation

Snapshot `withdrawalDelayBlocks` into the `WithdrawalRequest` struct at initiation time and use the stored value at completion time, mirroring the fix applied in the Bancor report:

```solidity
struct WithdrawalRequest {
    uint256 rsETHUnstaked;
    uint256 expectedAssetAmount;
    uint256 withdrawalStartBlock;
    uint256 withdrawalDelayBlocksSnapshot; // ← add this
}
```

In `_addUserWithdrawalRequest`:
```solidity
withdrawalRequests[requestId] = WithdrawalRequest({
    rsETHUnstaked: rsETHUnstaked,
    expectedAssetAmount: expectedAssetAmount,
    withdrawalStartBlock: block.number,
    withdrawalDelayBlocksSnapshot: withdrawalDelayBlocks  // ← snapshot here
});
```

In `_processWithdrawalCompletion`:
```solidity
if (block.number < request.withdrawalStartBlock + request.withdrawalDelayBlocksSnapshot)
    revert WithdrawalDelayNotPassed();
```

This ensures each request is governed by the delay that was in effect when the user committed their rsETH.

---

### Proof of Concept

1. Protocol initializes with `withdrawalDelayBlocks = 8 days / 12 seconds` (~57,600 blocks).
2. Alice calls `initiateWithdrawal(ETH, 1e18, "")`. Her rsETH is transferred to the contract. Her `WithdrawalRequest` records `withdrawalStartBlock = N`.
3. At block `N + 50,000` (still within the original 8-day window), the manager calls `setWithdrawalDelayBlocks(16 days / 12 seconds)` (~115,200 blocks) for a legitimate security reason.
4. Alice calls `completeWithdrawal` at block `N + 57,600` (past the original 8-day delay). The check `block.number < N + 115,200` is true → `revert WithdrawalDelayNotPassed()`.
5. Alice's funds remain locked for an additional ~8 days she did not consent to, with no ability to cancel or reclaim her rsETH. [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```

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

**File:** contracts/LRTWithdrawalManager.sol (L338-343)
```text
    function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
        // Set an upper limit of no more than 16 days
        if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();

        withdrawalDelayBlocks = withdrawalDelayBlocks_;
        emit WithdrawalDelayBlocksUpdated(withdrawalDelayBlocks);
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
