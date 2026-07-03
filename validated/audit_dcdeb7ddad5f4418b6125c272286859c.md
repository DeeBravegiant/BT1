### Title
Global `withdrawalDelayBlocks` Not Snapshotted Per Request Causes Retroactive Delay Changes on Pending Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager` stores a single global `withdrawalDelayBlocks` value that is applied at completion time against every pending withdrawal request's `withdrawalStartBlock`. Because the delay is never captured into the `WithdrawalRequest` struct at request time, any manager update to `withdrawalDelayBlocks` retroactively extends or shortens the wait for all already-queued requests.

### Finding Description
The `WithdrawalRequest` struct contains only three fields:

```solidity
struct WithdrawalRequest {
    uint256 rsETHUnstaked;
    uint256 expectedAssetAmount;
    uint256 withdrawalStartBlock;   // ← delay NOT stored here
}
``` [1](#0-0) 

The global delay is declared as a single contract-level variable:

```solidity
uint256 public withdrawalDelayBlocks;
``` [2](#0-1) 

It is checked at completion time in `_processWithdrawalCompletion`:

```solidity
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
``` [3](#0-2) 

And again in `_unlockWithdrawalRequests`, which gates the operator-triggered unlock step:

```solidity
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
``` [4](#0-3) 

The manager can update this value at any time via `setWithdrawalDelayBlocks`, which has no lower-bound guard and allows values up to 16 days:

```solidity
function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
    if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();
    withdrawalDelayBlocks = withdrawalDelayBlocks_;
    emit WithdrawalDelayBlocksUpdated(withdrawalDelayBlocks);
}
``` [5](#0-4) 

The default is initialized to 8 days:

```solidity
withdrawalDelayBlocks = 8 days / 12 seconds;
``` [6](#0-5) 

### Impact Explanation
**Temporary freezing of funds (Medium).** A user who calls `initiateWithdrawal` expecting an 8-day delay can have that delay retroactively extended to up to 16 days if the manager updates `withdrawalDelayBlocks` after the request is queued. Both the operator-triggered `unlockAssets` path (via `_unlockWithdrawalRequests`) and the user-triggered `completeWithdrawal` path (via `_processWithdrawalCompletion`) read the live global value, so the user's funds remain locked in the contract beyond the period they agreed to at request time. Conversely, a decrease allows early unlock, bypassing the intended security delay. [7](#0-6) 

### Likelihood Explanation
**Medium.** The `onlyLRTManager` role is a privileged but operationally active role expected to tune protocol parameters. A legitimate parameter update — e.g., adjusting the delay in response to EigenLayer withdrawal period changes — will silently and retroactively affect all pending requests without any per-request isolation. No malicious intent is required; the design flaw manifests from any routine manager update. [5](#0-4) 

### Recommendation
Snapshot `withdrawalDelayBlocks` into the `WithdrawalRequest` struct at the time `initiateWithdrawal` is called:

```solidity
struct WithdrawalRequest {
    uint256 rsETHUnstaked;
    uint256 expectedAssetAmount;
    uint256 withdrawalStartBlock;
    uint256 withdrawalDelayBlocks; // ← snapshot at request time
}
```

Then in `_addUserWithdrawalRequest`, assign:

```solidity
withdrawalRequests[requestId] = WithdrawalRequest({
    rsETHUnstaked: rsETHUnstaked,
    expectedAssetAmount: expectedAssetAmount,
    withdrawalStartBlock: block.number,
    withdrawalDelayBlocks: withdrawalDelayBlocks  // ← capture current value
});
```

And replace all reads of the global `withdrawalDelayBlocks` in `_processWithdrawalCompletion` and `_unlockWithdrawalRequests` with `request.withdrawalDelayBlocks`. [8](#0-7) 

### Proof of Concept
1. `withdrawalDelayBlocks` is initialized to `8 days / 12 seconds` (~57,600 blocks).
2. Alice calls `initiateWithdrawal(ETH, amount)` at block N. Her `WithdrawalRequest.withdrawalStartBlock = N`. She expects to complete at block N + 57,600.
3. The manager calls `setWithdrawalDelayBlocks(16 days / 12 seconds)` (~115,200 blocks).
4. Alice's `completeWithdrawal` call at block N + 57,600 now reverts with `WithdrawalDelayNotPassed` because `block.number < N + 115,200`.
5. Alice's ETH is locked for an additional ~8 days beyond what she agreed to, with no recourse.

The same path applies to the operator-triggered `unlockAssets` → `_unlockWithdrawalRequests` flow, where the `break` at line 795 will halt unlocking of all requests that were queued before the delay increase. [9](#0-8)

### Citations

**File:** contracts/interfaces/ILRTWithdrawalManager.sol (L39-43)
```text
    struct WithdrawalRequest {
        uint256 rsETHUnstaked;
        uint256 expectedAssetAmount;
        uint256 withdrawalStartBlock;
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L36-36)
```text
    uint256 public withdrawalDelayBlocks;
```

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
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

**File:** contracts/LRTWithdrawalManager.sol (L744-759)
```text
    function _addUserWithdrawalRequest(address asset, uint256 rsETHUnstaked, uint256 expectedAssetAmount) internal {
        uint256 nextUnusedNonce_ = nextUnusedNonce[asset];

        // Generate a unique identifier for the new withdrawal request.
        bytes32 requestId = getRequestId(asset, nextUnusedNonce_);

        // Create and store the new withdrawal request.
        withdrawalRequests[requestId] = WithdrawalRequest({
            rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
        });

        // Map the user to the newly created request index and increment the nonce for future requests.
        userAssociatedNonces[asset][msg.sender].pushBack(nextUnusedNonce_);
        nextUnusedNonce[asset] = nextUnusedNonce_ + 1;

        emit AssetWithdrawalQueued(msg.sender, asset, rsETHUnstaked, nextUnusedNonce_);
```

**File:** contracts/LRTWithdrawalManager.sol (L790-795)
```text
        while (nextLockedNonce_ < firstExcludedIndex) {
            bytes32 requestId = getRequestId(asset, nextLockedNonce_);
            WithdrawalRequest storage request = withdrawalRequests[requestId];

            // Check that the withdrawal delay has passed since the request's initiation.
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
```
