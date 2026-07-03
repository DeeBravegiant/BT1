### Title
Retroactive Application of Increased `withdrawalDelayBlocks` Temporarily Freezes Pending Withdrawal Requests - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager` stores a single global `withdrawalDelayBlocks` value that is applied at completion-time against every pending withdrawal's `withdrawalStartBlock`. When the LRT manager legitimately increases this value via `setWithdrawalDelayBlocks`, all already-queued withdrawal requests are retroactively subject to the longer delay, temporarily freezing funds for users who initiated withdrawals under the prior, shorter delay.

### Finding Description
`withdrawalDelayBlocks` is a single mutable state variable: [1](#0-0) 

It is initialized to 8 days at deployment: [2](#0-1) 

The LRT manager can update it at any time, up to 16 days: [3](#0-2) 

Both the operator-facing `_unlockWithdrawalRequests` and the user-facing `_processWithdrawalCompletion` evaluate the delay using the **current** global value against the request's stored `withdrawalStartBlock`: [4](#0-3) [5](#0-4) 

Because `withdrawalDelayBlocks` is read at execution time rather than being snapshotted into the `WithdrawalRequest` struct at queue time, any increase to the delay retroactively extends the waiting period for every request already sitting in the queue. A user who called `initiateWithdrawal` when the delay was 8 days and expected to complete after 8 days will find their request blocked if the manager raises the delay to, say, 16 days before the 8-day window elapses.

The `WithdrawalRequest` struct only stores `withdrawalStartBlock`, not the delay that was in effect at queue time: [6](#0-5) 

### Impact Explanation
Users who have already burned rsETH and queued a withdrawal request have their funds temporarily frozen beyond the delay they agreed to at initiation time. The rsETH is already held by the contract (transferred in `initiateWithdrawal`), so the user cannot cancel and re-enter; they must simply wait out the extended delay. This constitutes **temporary freezing of funds** (Medium severity). [7](#0-6) 

### Likelihood Explanation
The LRT manager role is an operational role expected to tune protocol parameters. Increasing `withdrawalDelayBlocks` is a plausible and legitimate action (e.g., in response to a security incident or to align with EigenLayer unbonding periods). No malicious intent is required; a routine parameter update is sufficient to trigger the freeze for all users with pending requests at that moment.

### Recommendation
Snapshot the effective delay into each `WithdrawalRequest` at queue time and use that stored value for the delay check, rather than reading the current global `withdrawalDelayBlocks`. Concretely, add a `withdrawalDelayBlocks` field to the `WithdrawalRequest` struct and populate it in `_addUserWithdrawalRequest`. Both `_unlockWithdrawalRequests` and `_processWithdrawalCompletion` should then compare against `request.withdrawalDelayBlocks` instead of the global variable.

### Proof of Concept

1. `withdrawalDelayBlocks` is initialized to `8 days / 12 seconds` (~57,600 blocks).
2. Alice calls `initiateWithdrawal(asset, amount, "")`. Her `WithdrawalRequest.withdrawalStartBlock` is set to `block.number = N`. She expects to complete withdrawal at block `N + 57,600`.
3. At block `N + 40,000` (still within the 8-day window), the LRT manager calls `setWithdrawalDelayBlocks(16 days / 12 seconds)` (~115,200 blocks) for a legitimate security reason.
4. Alice attempts `completeWithdrawal` at block `N + 57,600`. The check `block.number < request.withdrawalStartBlock + withdrawalDelayBlocks` evaluates as `N + 57,600 < N + 115,200` → **reverts with `WithdrawalDelayNotPassed`**.
5. Alice's rsETH is already held by the contract and cannot be reclaimed. She must wait an additional ~57,600 blocks (~8 days) beyond her original expectation. [5](#0-4) [8](#0-7)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L36-36)
```text
    uint256 public withdrawalDelayBlocks;
```

**File:** contracts/LRTWithdrawalManager.sol (L94-94)
```text
        withdrawalDelayBlocks = 8 days / 12 seconds;
```

**File:** contracts/LRTWithdrawalManager.sol (L166-166)
```text
        IERC20(lrtConfig.rsETH()).safeTransferFrom(msg.sender, address(this), rsETHUnstaked);
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

**File:** contracts/LRTWithdrawalManager.sol (L795-795)
```text
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
