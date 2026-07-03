### Title
Mutable `withdrawalDelayBlocks` Applied at Completion Time Retroactively Freezes In-Flight Withdrawals — (`contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager` stores only `withdrawalStartBlock` in each `WithdrawalRequest` struct, but evaluates the delay at completion time by reading the current global `withdrawalDelayBlocks`. Because the LRT manager can call `setWithdrawalDelayBlocks` at any time, an increase to this parameter retroactively extends the lock period for all already-queued withdrawal requests, temporarily freezing user funds that were submitted under a shorter delay commitment.

---

### Finding Description

When a user calls `initiateWithdrawal`, their rsETH is transferred into the contract and a `WithdrawalRequest` is created storing only three fields:

```solidity
// contracts/LRTWithdrawalManager.sol:751-753
withdrawalRequests[requestId] = WithdrawalRequest({
    rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
});
``` [1](#0-0) 

The `withdrawalDelayBlocks` value at the time of submission is **not** captured. Later, both the unlock step and the completion step read the current global value:

```solidity
// _unlockWithdrawalRequests — line 795
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;

// _processWithdrawalCompletion — line 715
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
``` [2](#0-1) [3](#0-2) 

The manager can change this value at any time up to the hard cap of 16 days:

```solidity
// contracts/LRTWithdrawalManager.sol:338-343
function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
    if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();
    withdrawalDelayBlocks = withdrawalDelayBlocks_;
    ...
}
``` [4](#0-3) 

The `WithdrawalRequest` struct in the interface confirms no delay field is stored per-request:

```solidity
struct WithdrawalRequest {
    uint256 rsETHUnstaked;
    uint256 expectedAssetAmount;
    uint256 withdrawalStartBlock;
}
``` [5](#0-4) 

By contrast, `KernelDepositPool.initiateWithdrawal` correctly snapshots the delay into the struct at submission time (`unlockTime = block.timestamp + withdrawalDelay`), so a later change to `withdrawalDelay` cannot affect existing requests: [6](#0-5) 

`LRTWithdrawalManager` lacks this protection.

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

A user submits `initiateWithdrawal` under the current `withdrawalDelayBlocks` (e.g., 8 days). Their rsETH is immediately transferred to the contract. If the manager subsequently increases `withdrawalDelayBlocks` to 16 days (the maximum), the user's withdrawal is blocked for an additional 8 days beyond what was committed at submission time. The effect applies to:

1. Requests not yet processed by `unlockQueue` — the unlock loop breaks early at line 795.
2. Requests already unlocked — `_processWithdrawalCompletion` re-checks the delay at line 715 and reverts.

In both cases the user's rsETH is held in the contract and cannot be recovered until the new delay elapses (or the manager reduces the delay again). The funds are not permanently lost, making this a temporary freeze.

---

### Likelihood Explanation

**Medium.** The manager role is a privileged but non-timelock-protected role that can call `setWithdrawalDelayBlocks` at any time. A legitimate security-motivated increase (e.g., responding to an incident) would silently retroact on all pending requests. No attacker action is required; the manager acting in good faith is sufficient to trigger the freeze. Given that the protocol actively manages withdrawal parameters and the delay range spans 0–16 days, parameter changes are expected operational events.

---

### Recommendation

Snapshot `withdrawalDelayBlocks` into the `WithdrawalRequest` struct at submission time, mirroring the pattern used in `KernelDepositPool`:

```solidity
struct WithdrawalRequest {
    uint256 rsETHUnstaked;
    uint256 expectedAssetAmount;
    uint256 withdrawalStartBlock;
    uint256 withdrawalDelayBlocks; // snapshot at submission
}
```

In `_addUserWithdrawalRequest`, populate the new field:

```solidity
withdrawalRequests[requestId] = WithdrawalRequest({
    rsETHUnstaked: rsETHUnstaked,
    expectedAssetAmount: expectedAssetAmount,
    withdrawalStartBlock: block.number,
    withdrawalDelayBlocks: withdrawalDelayBlocks
});
```

Replace both runtime reads of the global `withdrawalDelayBlocks` with `request.withdrawalDelayBlocks` in `_unlockWithdrawalRequests` (line 795) and `_processWithdrawalCompletion` (line 715).

---

### Proof of Concept

1. `withdrawalDelayBlocks` is initialized to `8 days / 12 seconds` (~57,600 blocks). [7](#0-6) 

2. Alice calls `initiateWithdrawal(ETH, 1e18, "")`. Her rsETH is transferred to the contract. Her `WithdrawalRequest` records `withdrawalStartBlock = N`.

3. The LRT manager calls `setWithdrawalDelayBlocks(16 days / 12 seconds)` (~115,200 blocks) for a legitimate security reason.

4. At block `N + 57,600` (8 days later), Alice calls `completeWithdrawal`. The check at line 715 evaluates:
   ```
   block.number (N+57600) < N + 115200  →  true  →  revert WithdrawalDelayNotPassed
   ```
   Alice's funds are frozen for an additional 8 days she never agreed to.

5. Even if Alice's request was already unlocked by a prior `unlockQueue` call, the same check at line 715 in `_processWithdrawalCompletion` still reverts, freezing already-unlocked funds. [3](#0-2)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```

**File:** contracts/LRTWithdrawalManager.sol (L338-343)
```text
    function setWithdrawalDelayBlocks(uint256 withdrawalDelayBlocks_) external onlyLRTManager {
        // Set an upper limit of no more than 16 days
        if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();

        withdrawalDelayBlocks = withdrawalDelayBlocks_;
        emit WithdrawalDelayBlocksUpdated(withdrawalDelayBlocks);
```

**File:** contracts/LRTWithdrawalManager.sol (L714-716)
```text
        // Check that the withdrawal delay has passed since the request's initiation.
        if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();

```

**File:** contracts/LRTWithdrawalManager.sol (L751-753)
```text
        withdrawalRequests[requestId] = WithdrawalRequest({
            rsETHUnstaked: rsETHUnstaked, expectedAssetAmount: expectedAssetAmount, withdrawalStartBlock: block.number
        });
```

**File:** contracts/LRTWithdrawalManager.sol (L793-796)
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L330-334)
```text
        uint256 unlockTime = block.timestamp + withdrawalDelay;

        withdrawals[withdrawalId] = Withdrawal({
            user: msg.sender, amount: _amount, unlockTime: unlockTime, claimed: false, withdrawalId: withdrawalId
        });
```
