### Title
Global `withdrawalDelayBlocks` Applies Retroactively to Already-Unlocked Requests, Causing Temporary Fund Freeze — (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager` stores only `withdrawalStartBlock` per request and reads the current global `withdrawalDelayBlocks` at both unlock time and claim time. If the manager increases `withdrawalDelayBlocks` after a batch of requests has already been unlocked (i.e., `nextLockedNonce` has advanced past them), users whose rsETH was already burned cannot complete their withdrawals until the new, longer delay elapses from their original start block. Their funds are temporarily frozen with no recourse.

---

### Finding Description

`_addUserWithdrawalRequest` records only `withdrawalStartBlock` in the `WithdrawalRequest` struct; the delay is never snapshotted per request. [1](#0-0) 

Both the operator-facing unlock path and the user-facing claim path read the live global `withdrawalDelayBlocks`:

**Unlock path** (`_unlockWithdrawalRequests`, line 795):
```solidity
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
``` [2](#0-1) 

**Claim path** (`_processWithdrawalCompletion`, line 715):
```solidity
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
``` [3](#0-2) 

`setWithdrawalDelayBlocks` has an upper bound of 16 days but **no lower bound**, and takes effect immediately with no time-lock:

```solidity
function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
    if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();
    withdrawalDelayBlocks = withdrawalDelayBlocks_;
    ...
}
``` [4](#0-3) 

The two-phase lifecycle is:
1. **Locked → Unlocked**: operator calls `unlockQueue`; `nextLockedNonce[asset]` advances, rsETH is burned from the contract.
2. **Unlocked → Claimed**: user calls `completeWithdrawal`; assets are transferred.

Once a request passes phase 1, the user's rsETH is gone. If the manager increases `withdrawalDelayBlocks` between phases 1 and 2, the claim-path delay check re-evaluates against the new (larger) value and reverts, even though the request is already in the "unlocked" state.

---

### Impact Explanation

**Temporary freezing of funds (Medium).**

After `unlockQueue` advances `nextLockedNonce` and burns the user's rsETH, the user holds no rsETH and no underlying asset — the asset is held by `LRTWithdrawalManager` (or Aave). If `withdrawalDelayBlocks` is raised from D to D′ before the user claims, `completeWithdrawal` reverts with `WithdrawalDelayNotPassed` for every block in the window `[withdrawalStartBlock + D, withdrawalStartBlock + D′)`. The freeze duration equals `(D′ − D) × 12 seconds` per affected request. Because the transaction reverts atomically, no storage is permanently corrupted, so the freeze is temporary — but users cannot access their funds during that window despite having already surrendered their rsETH.

---

### Likelihood Explanation

The manager role is a live operational key used for routine parameter changes. Increasing the withdrawal delay is a plausible response to a perceived security event (e.g., oracle anomaly, EigenLayer incident). The protocol already has a queue of unlocked-but-unclaimed requests at any given time (evidenced by `unlockedWithdrawalsCount`). A single `setWithdrawalDelayBlocks` call silently freezes all of them. No attacker action is required; the trigger is a legitimate, well-intentioned admin operation with an unintended retroactive side-effect.

---

### Recommendation

Snapshot the effective delay per request at creation time, exactly as `KernelDepositPool` does with `unlockTime = block.timestamp + withdrawalDelay`: [5](#0-4) 

Add a `withdrawalDelayBlocks` field to `WithdrawalRequest`, populate it in `_addUserWithdrawalRequest`, and replace both live reads of the global `withdrawalDelayBlocks` with `request.withdrawalDelayBlocks`. This ensures that changing the global parameter only affects future requests, not requests already in the queue.

---

### Proof of Concept

1. `withdrawalDelayBlocks` = 57,600 (8 days). User calls `initiateWithdrawal(ETH, 1e18, "")` at block **B**. rsETH is transferred to the contract.
2. At block **B + 57,600**, operator calls `unlockQueue(ETH, ...)`. The check `B + 57,600 < B + 57,600` is false → request is unlocked, `nextLockedNonce[ETH]` advances, rsETH is burned from the contract.
3. Manager calls `setWithdrawalDelayBlocks(115_200)` (16 days). Takes effect immediately.
4. User calls `completeWithdrawal(ETH, "")` at block **B + 57,601**.
   - Unlock check: `57,601 >= nextLockedNonce` → passes.
   - Delay check: `B + 57,601 < B + 115,200` → **reverts** `WithdrawalDelayNotPassed`.
5. User's rsETH is already burned. They cannot recover assets until block **B + 115,200** — an additional ~8 days of unexpected freeze. [6](#0-5)

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L794-795)
```text
            // Check that the withdrawal delay has passed since the request's initiation.
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L330-334)
```text
        uint256 unlockTime = block.timestamp + withdrawalDelay;

        withdrawals[withdrawalId] = Withdrawal({
            user: msg.sender, amount: _amount, unlockTime: unlockTime, claimed: false, withdrawalId: withdrawalId
        });
```
