### Title
Mutable `withdrawalDelayBlocks` Checked at Completion Time Temporarily Freezes User Funds - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager` checks the **current** `withdrawalDelayBlocks` value when a user calls `completeWithdrawal()`, rather than the value that was in effect when the user called `initiateWithdrawal()`. Because `setWithdrawalDelayBlocks()` can increase this parameter at any time, users who have already transferred their rsETH into the contract can be prevented from completing their withdrawal until the new (longer) delay has elapsed from their original `withdrawalStartBlock`.

### Finding Description
When a user calls `initiateWithdrawal()`, their rsETH is immediately transferred into the contract and a `WithdrawalRequest` is stored with only three fields: `rsETHUnstaked`, `expectedAssetAmount`, and `withdrawalStartBlock`. [1](#0-0) 

The delay that was in effect at initiation time is **not** cached. When the user later calls `completeWithdrawal()`, `_processWithdrawalCompletion()` enforces the delay using the **live** state variable: [2](#0-1) 

The LRT manager can raise `withdrawalDelayBlocks` up to the protocol maximum at any time: [3](#0-2) 

The default is `8 days / 12 seconds` and the ceiling is `16 days / 12 seconds`: [4](#0-3) [5](#0-4) 

### Impact Explanation
After a user calls `initiateWithdrawal()`, their rsETH is held by the contract. If the manager increases `withdrawalDelayBlocks` before the user calls `completeWithdrawal()`, the user's call reverts with `WithdrawalDelayNotPassed`. The user cannot recover their rsETH until the new, longer delay has elapsed from their original `withdrawalStartBlock`. This constitutes a **temporary freezing of user funds** (rsETH already surrendered, underlying asset not yet received).

Maximum additional freeze: up to `16 days - 8 days = 8 days` beyond the user's original expectation.

### Likelihood Explanation
The LRT manager role is a privileged but operationally active role that adjusts protocol parameters. A routine parameter update (e.g., extending the delay for security reasons during a market event) would inadvertently freeze all in-flight withdrawals. No malicious intent is required; the impact occurs as a side-effect of any upward adjustment to `withdrawalDelayBlocks` while withdrawals are pending.

### Recommendation
Cache `withdrawalDelayBlocks` as part of the `WithdrawalRequest` struct at initiation time, and use the cached value at completion time:

```solidity
struct WithdrawalRequest {
    uint256 rsETHUnstaked;
    uint256 expectedAssetAmount;
    uint256 withdrawalStartBlock;
    uint256 withdrawalDelayBlocks; // cache at initiation
}
```

In `_addUserWithdrawalRequest`, set `withdrawalDelayBlocks: withdrawalDelayBlocks`. In `_processWithdrawalCompletion`, replace:
```solidity
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
```
with:
```solidity
if (block.number < request.withdrawalStartBlock + request.withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
```

Apply the same fix to the `_unlockWithdrawalRequests` loop at line 795.

### Proof of Concept
1. `withdrawalDelayBlocks` is `8 days / 12 seconds` (~57,600 blocks). User calls `initiateWithdrawal(stETH, 1e18, "")`. rsETH is transferred to the contract. `withdrawalStartBlock = N`.
2. At block `N + 50,000` (still within the original 8-day window), the LRT manager calls `setWithdrawalDelayBlocks(16 days / 12 seconds)` (~115,200 blocks).
3. User calls `completeWithdrawal(stETH, "")` at block `N + 57,601` (past the original delay). The check `block.number < request.withdrawalStartBlock + withdrawalDelayBlocks` evaluates as `N+57,601 < N+115,200` → **true** → reverts with `WithdrawalDelayNotPassed`.
4. The user's rsETH remains locked in the contract for an additional ~8 days beyond their original expectation, with no recourse. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```

**File:** contracts/LRTWithdrawalManager.sol (L162-176)
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
