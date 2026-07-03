### Title
Manager Can Retroactively Extend `withdrawalDelayBlocks`, Temporarily Freezing Pending Withdrawal Requests - (File: contracts/LRTWithdrawalManager.sol)

---

### Summary

`LRTWithdrawalManager` stores only `withdrawalStartBlock` per request and reads the global `withdrawalDelayBlocks` at completion time. When the LRT manager calls `setWithdrawalDelayBlocks` to increase the delay, every existing pending request is retroactively re-locked, temporarily freezing user funds beyond the delay they accepted when initiating withdrawal.

---

### Finding Description

When a user calls `initiateWithdrawal`, the contract records only the block at which the request was created:

```solidity
withdrawalRequests[requestId] = WithdrawalRequest({
    rsETHUnstaked: rsETHUnstaked,
    expectedAssetAmount: expectedAssetAmount,
    withdrawalStartBlock: block.number   // only the start block is stored
});
``` [1](#0-0) 

The unlock eligibility check in `_unlockWithdrawalRequests` and the completion check in `_processWithdrawalCompletion` both compute the deadline on-the-fly using the **current** global `withdrawalDelayBlocks`:

```solidity
// in _unlockWithdrawalRequests
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
``` [2](#0-1) 

```solidity
// in _processWithdrawalCompletion
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
``` [3](#0-2) 

The LRT manager can update `withdrawalDelayBlocks` at any time, with only an upper-bound check (≤ 16 days):

```solidity
function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
    if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();
    withdrawalDelayBlocks = withdrawalDelayBlocks_;
    emit WithdrawalDelayBlocksUpdated(withdrawalDelayBlocks);
}
``` [4](#0-3) 

The contract is initialized with an 8-day delay:

```solidity
withdrawalDelayBlocks = 8 days / 12 seconds;
``` [5](#0-4) 

Because the deadline is never fixed at request time, increasing `withdrawalDelayBlocks` from 8 days to 16 days immediately re-locks every existing request that had already passed the old 8-day threshold, preventing both `unlockQueue` and `completeWithdrawal` from processing them.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

Users who initiated withdrawals under the 8-day delay have their rsETH already burned from their wallet and locked in the contract. After the manager increases the delay, those users cannot retrieve their underlying assets (ETH/LST) until the new, longer delay has elapsed. The funds are not permanently lost, but they are frozen beyond the timeline the user accepted at initiation time.

---

### Likelihood Explanation

The `onlyLRTManager` role is a privileged but operationally active role (not a multisig-only or governance-only role). Updating `withdrawalDelayBlocks` is a routine administrative action with no on-chain time-lock protecting it. Any time the manager legitimately adjusts the delay upward — even for benign operational reasons — all in-flight requests are retroactively affected. No malicious intent is required; the design flaw is structural.

---

### Recommendation

Store the absolute deadline block at request creation time instead of recomputing it from the current global parameter:

```solidity
// In _addUserWithdrawalRequest:
withdrawalRequests[requestId] = WithdrawalRequest({
    rsETHUnstaked: rsETHUnstaked,
    expectedAssetAmount: expectedAssetAmount,
    withdrawalStartBlock: block.number,
    withdrawalDeadlineBlock: block.number + withdrawalDelayBlocks  // fix: snapshot at creation
});
```

Then replace both deadline checks with:

```solidity
if (block.number < request.withdrawalDeadlineBlock) revert WithdrawalDelayNotPassed();
```

This mirrors the recommendation in the reference report: store the absolute expiry at the moment the user commits, so subsequent parameter changes cannot retroactively alter existing positions.

---

### Proof of Concept

1. `withdrawalDelayBlocks` = 57,600 blocks (8 days). Alice calls `initiateWithdrawal` at block **N**. Her rsETH is transferred to the contract. She expects to complete withdrawal at block **N + 57,600**.
2. At block **N + 55,000** (day 7.6), the LRT manager calls `setWithdrawalDelayBlocks(115_200)` (16 days) — a routine adjustment.
3. At block **N + 57,600**, Alice calls `completeWithdrawal`. The check evaluates:
   `block.number (57,600) < request.withdrawalStartBlock (0) + withdrawalDelayBlocks (115,200)` → **true** → `revert WithdrawalDelayNotPassed()`.
4. Alice's funds are frozen for an additional ~8 days beyond what she agreed to, with no recourse. She cannot cancel the request or recover her rsETH.

### Citations

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

**File:** contracts/LRTWithdrawalManager.sol (L715-715)
```text
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
```

**File:** contracts/LRTWithdrawalManager.sol (L751-753)
```text
        withdrawalRequests[requestId] = WithdrawalRequest({
            rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
        });
```

**File:** contracts/LRTWithdrawalManager.sol (L795-795)
```text
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
```
