### Title
Block-Number-Based Withdrawal Delay Hardcodes 12-Second Block Time, Making Delay Non-Robust Against Block Time Changes - (File: contracts/LRTWithdrawalManager.sol)

### Summary
`LRTWithdrawalManager` measures the withdrawal delay using `block.number` and initializes `withdrawalDelayBlocks` by dividing a wall-clock duration by a hardcoded 12-second block time. If the actual average block time deviates from 12 seconds, the real-time delay experienced by users diverges from the intended 8-day window — either locking funds longer than promised (temporary freeze) or releasing them earlier than intended.

### Finding Description
The contract initializes the withdrawal delay as:

```solidity
// contracts/LRTWithdrawalManager.sol:94
withdrawalDelayBlocks = 8 days / 12 seconds;  // = 57,600 blocks
```

The setter enforces an upper bound using the same assumption:

```solidity
// contracts/LRTWithdrawalManager.sol:340
if (withdrawalDelayBlocks_ > 16 days / 12 seconds) revert ExceedWithdrawalDelay();
```

When a user calls `initiateWithdrawal()`, the current block number is stored:

```solidity
// contracts/LRTWithdrawalManager.sol:752
withdrawalStartBlock: block.number
```

Both the operator-facing `_unlockWithdrawalRequests()` and the user-facing `_processWithdrawalCompletion()` enforce the delay by comparing block numbers:

```solidity
// contracts/LRTWithdrawalManager.sol:795
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) break;

// contracts/LRTWithdrawalManager.sol:715
if (block.number < request.withdrawalStartBlock + withdrawalDelayBlocks) revert WithdrawalDelayNotPassed();
```

The entire delay mechanism is anchored to block counts, not wall-clock seconds. The 12-second divisor is a compile-time constant baked into both the initializer and the setter's upper-bound check. There is no on-chain mechanism to recalibrate the block count if the network's average block time shifts.

### Impact Explanation
- **If average block time increases** (e.g., due to network congestion or a future protocol change): 57,600 blocks represent more than 8 real-time days. Users' funds are locked longer than the protocol promises — a **temporary freezing of funds**.
- **If average block time decreases** (e.g., a future Ethereum upgrade): 57,600 blocks represent fewer than 8 real-time days. Users can complete withdrawals before the intended 8-day window elapses. Since EigenLayer's own withdrawal delay is also block-based (50,400 blocks), the relative ordering is preserved in block terms, so direct insolvency is unlikely — but the protocol **fails to deliver its promised 8-day delay**.

Impact classification: **Low — Contract fails to deliver promised returns / Medium — Temporary freezing of funds** depending on the direction of block time drift.

### Likelihood Explanation
Ethereum mainnet post-merge block times are stable at approximately 12 seconds. However, the hardcoded assumption is a structural fragility: any future protocol upgrade (e.g., single-slot finality, changes to slot timing) or sustained deviation in block production would silently miscalibrate the delay without any on-chain signal. The likelihood of a meaningful deviation is low today but non-zero over the protocol's lifetime.

### Recommendation
Replace block-number-based delay tracking with timestamp-based tracking. Store `block.timestamp` at request creation and compare against a `withdrawalDelaySeconds` constant (e.g., `8 days`) at unlock/claim time:

```solidity
// Store timestamp instead of block number
withdrawalStartTime: block.timestamp

// Check elapsed seconds instead of elapsed blocks
if (block.timestamp < request.withdrawalStartTime + withdrawalDelaySeconds) revert WithdrawalDelayNotPassed();
```

Remove the hardcoded `/ 12 seconds` divisors from both `initialize()` and `setWithdrawalDelayBlocks()`. This mirrors the recommendation in the referenced external report: measure time in seconds rather than blocks to remain robust against changes to average block time.

### Proof of Concept

1. `initialize()` sets `withdrawalDelayBlocks = 8 days / 12 seconds = 57,600`. [1](#0-0) 

2. `setWithdrawalDelayBlocks()` enforces an upper bound using the same 12-second assumption. [2](#0-1) 

3. `_addUserWithdrawalRequest()` records `block.number` as the start of the delay. [3](#0-2) 

4. `_unlockWithdrawalRequests()` and `_processWithdrawalCompletion()` enforce the delay by block-number arithmetic. [4](#0-3) [5](#0-4) 

**Scenario — block time increases to 14 seconds:**
- Intended delay: 8 days = 691,200 seconds
- Actual delay: 57,600 blocks × 14 s/block = 806,400 seconds ≈ **9.33 days**
- Users' rsETH is locked for ~1.33 extra days beyond the protocol's stated guarantee, constituting a temporary freeze of user funds.

**Scenario — block time decreases to 10 seconds:**
- Actual delay: 57,600 blocks × 10 s/block = 576,000 seconds ≈ **6.67 days**
- The protocol delivers a shorter delay than its stated 8-day window, failing to deliver promised returns.

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
