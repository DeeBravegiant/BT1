### Title
Missing Guard on `unlockedWithdrawalsCount` Decrement Causes Opaque Underflow Revert, Freezing User Withdrawals - (File: contracts/LRTWithdrawalManager.sol)

### Summary
In `_processWithdrawalCompletion`, the statement `unlockedWithdrawalsCount[asset]--` at line 717 is executed without first verifying that `unlockedWithdrawalsCount[asset] > 0`. Under Solidity 0.8+ checked arithmetic, if the counter is zero, the transaction reverts with an opaque arithmetic panic (0x11) rather than a meaningful error. Users whose rsETH is already held by the contract cannot complete their withdrawals, temporarily freezing their funds.

### Finding Description
`_processWithdrawalCompletion` is the internal function called by both `completeWithdrawal` and `completeWithdrawalForUser`. After verifying the request is unlocked (`usersFirstWithdrawalRequestNonce < nextLockedNonce[asset]`) and the delay has passed, it unconditionally decrements the counter:

```solidity
// contracts/LRTWithdrawalManager.sol line 717
unlockedWithdrawalsCount[asset]--;
```

`unlockedWithdrawalsCount` is a `uint256` mapping seeded by the privileged `initialize2` / `initialize3` reinitializers and incremented inside `_unlockWithdrawalRequests`. The seeding logic in `UnlockedWithdrawalsInitializer` counts only requests where `expectedAssetAmount > 0`. If any unlocked request had its `expectedAssetAmount` set to zero (e.g., when `_calculatePayoutAmount` returns 0 due to extreme price conditions), the seeded count will be lower than the actual number of completable requests. Once the counter reaches zero, every subsequent `completeWithdrawal` call for a still-valid unlocked request reverts with an arithmetic underflow panic — no custom error, no explanation.

### Impact Explanation
Users who called `initiateWithdrawal` (transferring their rsETH to the contract) and whose requests were subsequently unlocked by the operator are unable to call `completeWithdrawal` successfully. Their rsETH is held in the contract and cannot be recovered until an admin upgrade corrects the counter. This constitutes a **temporary freezing of funds** (Medium).

### Likelihood Explanation
The counter desync requires the `initialize2` seeding to undercount, which happens when any unlocked request has `expectedAssetAmount == 0` at seeding time. `_calculatePayoutAmount` can return 0 if `rsETHPrice` is very low relative to `assetPrice`. The `UnlockedWithdrawalsInitializer` explicitly filters on `expectedAssetAmount > 0`, making this a realistic edge case for any deployment that went through the upgrade path.

### Recommendation
Add an explicit guard before the decrement, mirroring the pattern used in `LRTUnstakingVault.decreaseUncompletedWithdrawalCount`:

```solidity
// Before line 717
if (unlockedWithdrawalsCount[asset] == 0) revert UnlockedWithdrawalsCountUnderflow();
unlockedWithdrawalsCount[asset]--;
```

This provides a clear, actionable error instead of an opaque arithmetic panic.

### Proof of Concept

1. Protocol is upgraded; `initialize2` is called. The `UnlockedWithdrawalsInitializer` counts only requests with `expectedAssetAmount > 0`, seeding `unlockedWithdrawalsCount[ETH] = N`.
2. Suppose one unlocked request (nonce `k < nextLockedNonce[ETH]`) had `expectedAssetAmount` set to 0 by `_calculatePayoutAmount` during unlock. The true completable count is `N + 1`, but the counter is `N`.
3. `N` users successfully call `completeWithdrawal(ETH, ...)`. Each call decrements the counter; after the Nth call, `unlockedWithdrawalsCount[ETH] == 0`.
4. The `(N+1)`th user — whose request is valid (`nonce < nextLockedNonce`, delay passed, queue non-empty) — calls `completeWithdrawal(ETH, ...)`.
5. Execution reaches line 717: `unlockedWithdrawalsCount[ETH]--` → arithmetic underflow panic (0x11). Transaction reverts with no meaningful message.
6. The user's rsETH (transferred at `initiateWithdrawal` line 166) remains locked in `LRTWithdrawalManager` with no recovery path until an admin upgrade. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L162-166)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
        }

        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L699-717)
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
```

**File:** contracts/LRTWithdrawalManager.sol (L802-809)
```text
            assetsCommitted[asset] -= request.expectedAssetAmount;
            // Set the amount the user will receive
            request.expectedAssetAmount = payoutAmount;
            rsETHAmountToBurn += request.rsETHUnstaked;
            availableAssetAmount -= payoutAmount;
            assetAmountToUnlock += payoutAmount;

            unlockedWithdrawalsCount[asset]++;
```
