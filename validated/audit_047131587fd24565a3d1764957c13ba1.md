### Title
Manager can retroactively extend withdrawal delay for all in-flight requests, temporarily freezing user funds — (File: `contracts/LRTWithdrawalManager.sol`)

---

### Summary
`setWithdrawalDelayBlocks` updates the global `withdrawalDelayBlocks` with no restriction on whether pending withdrawal requests already exist. Because both the `_processWithdrawalCompletion` and `_unlockWithdrawalRequests` paths read the **current** global value at execution time rather than the value snapshotted at request initiation, the LRT Manager can retroactively extend the lock period for every already-queued withdrawal.

---

### Finding Description

When a user calls `initiateWithdrawal`, the contract records only `withdrawalStartBlock: block.number` in the `WithdrawalRequest` struct: [1](#0-0) 

The delay itself is **not** snapshotted into the struct. Both downstream checks read the live global `withdrawalDelayBlocks`:

**In `_processWithdrawalCompletion`:** [2](#0-1) 

**In `_unlockWithdrawalRequests`:** [3](#0-2) 

The LRT Manager can call `setWithdrawalDelayBlocks` at any time, with no guard against existing pending requests: [4](#0-3) 

The only constraint is an upper ceiling of `16 days / 12 seconds` (≈ 115,200 blocks). The default at initialization is `8 days / 12 seconds` (≈ 57,600 blocks): [5](#0-4) 

Increasing `withdrawalDelayBlocks` after users have already queued requests silently extends their lock period by the full delta — up to an additional 8 days — without any notification or recourse.

Note: `KernelDepositPool.setWithdrawalDelay` does **not** share this flaw because it snapshots `unlockTime = block.timestamp + withdrawalDelay` into each `Withdrawal` struct at initiation time, so future changes to `withdrawalDelay` cannot affect already-pending records: [6](#0-5) 

---

### Impact Explanation

**Temporary freezing of funds (Medium).** Users who have already burned rsETH and queued a withdrawal request — with a concrete expectation of when they can complete it — can have that unlock block pushed arbitrarily further into the future (up to 16 days from their `withdrawalStartBlock`). Their rsETH has already been transferred to the contract; they cannot cancel and re-enter. The funds are not permanently lost, but they are inaccessible for longer than the user agreed to at initiation time.

---

### Likelihood Explanation

**Low.** The LRT Manager must actively call `setWithdrawalDelayBlocks` with a higher value while pending requests exist. This could occur intentionally (to delay a wave of withdrawals during a market stress event) or unintentionally (a routine parameter update during a period with many queued requests). The Manager role is a live operational key, not a multisig-only governance action, making accidental or coerced misuse realistic.

---

### Recommendation

Snapshot `withdrawalDelayBlocks` into the `WithdrawalRequest` struct at initiation time and use that stored value for all subsequent checks:

```solidity
struct WithdrawalRequest {
    uint256 rsETHUnstaked;
    uint256 expectedAssetAmount;
    uint256 withdrawalStartBlock;
    uint256 withdrawalDelayBlocksSnapshot; // add this
}
```

In `_addUserWithdrawalRequest`:
```solidity
withdrawalRequests[requestId] = WithdrawalRequest({
    rsETHUnstaked: rsETHUnstaked,
    expectedAssetAmount: expectedAssetAmount,
    withdrawalStartBlock: block.number,
    withdrawalDelayBlocksSnapshot: withdrawalDelayBlocks
});
```

Replace both live-read checks with the per-request snapshot:
```solidity
// _processWithdrawalCompletion
if (block.number < request.withdrawalStartBlock + request.withdrawalDelayBlocksSnapshot)
    revert WithdrawalDelayNotPassed();

// _unlockWithdrawalRequests
if (block.number < request.withdrawalStartBlock + request.withdrawalDelayBlocksSnapshot) break;
```

---

### Proof of Concept

1. `withdrawalDelayBlocks` is initialized to `57,600` blocks (8 days).
2. Alice calls `initiateWithdrawal(ETH, amount, "")` at block `N`. Her rsETH is transferred to the contract. She expects to call `completeWithdrawal` at block `N + 57,600`.
3. LRT Manager calls `setWithdrawalDelayBlocks(115_200)` (16 days) — no revert, no restriction.
4. Alice calls `completeWithdrawal(ETH, "")` at block `N + 57,600`.
5. `_processWithdrawalCompletion` evaluates: `block.number (N+57,600) < request.withdrawalStartBlock (N) + withdrawalDelayBlocks (115,200)` → `true` → `revert WithdrawalDelayNotPassed()`.
6. Alice's ETH is frozen for an additional 57,600 blocks (~8 days) beyond what she agreed to at initiation, with no ability to cancel.

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

**File:** contracts/LRTWithdrawalManager.sol (L714-715)
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

**File:** contracts/LRTWithdrawalManager.sol (L794-795)
```text
            // Check that the withdrawal delay has passed since the request's initiation.
            if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L329-334)
```text
        uint256 withdrawalId = ++withdrawalCounter;
        uint256 unlockTime = block.timestamp + withdrawalDelay;

        withdrawals[withdrawalId] = Withdrawal({
            user: msg.sender, amount: _amount, unlockTime: unlockTime, claimed: false, withdrawalId: withdrawalId
        });
```
