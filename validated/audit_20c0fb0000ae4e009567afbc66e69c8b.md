### Title
Rounding Down in `notifyRewardAmount()` Permanently Locks Reward Tokens With No Recovery Mechanism - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.notifyRewardAmount()` computes `rewardRate` via integer division of the received reward amount by `duration`. The truncated remainder (`receivedAmount % duration`) is transferred into the contract but can never be distributed or recovered, permanently freezing a portion of yield on every reward epoch.

### Finding Description
In `contracts/KERNEL/KernelDepositPool.sol`, `notifyRewardAmount()` sets the per-second reward rate using integer division:

```solidity
// Line 580 (new period)
rewardRate = receivedAmount / duration;

// Line 583 (mid-period top-up)
rewardRate = (receivedAmount + remaining) / duration;
```

Because Solidity integer division truncates toward zero, `rewardRate * duration` is always `≤ receivedAmount` (or `≤ receivedAmount + remaining`). The difference — up to `duration - 1` tokens per call — is transferred into the contract at line 574 but is never accounted for in any distribution path. The contract contains no `rescue`, `recover`, or `sweep` function, and `setRewardsDuration` is gated behind `finishAt >= block.timestamp`, so there is no admin path to reclaim the dust either. [1](#0-0) [2](#0-1) 

### Impact Explanation
Every invocation of `notifyRewardAmount()` permanently locks up to `duration - 1` reward tokens. With a `duration` of, e.g., 7 days (604,800 seconds), the maximum dust per call is 604,799 units of the reward token. Across multiple epochs this accumulates. KERNEL stakers — the reward claimants — receive strictly less yield than was deposited into the contract, with no path to recover the difference. This matches **Medium: Permanent freezing of unclaimed yield**. [3](#0-2) 

### Likelihood Explanation
This is not a conditional edge case — integer division truncation occurs on **every** `notifyRewardAmount()` call unless `receivedAmount` is exactly divisible by `duration`, which is practically never guaranteed. The contract is already deployed with a live reward mechanism, so the loss accumulates with each reward epoch. [1](#0-0) 

### Recommendation
Carry the remainder forward into the next epoch by tracking it explicitly:

```solidity
uint256 dust = receivedAmount % duration;
// add dust back to a pendingRewards accumulator, or
// include it in the next notifyRewardAmount call
```

Alternatively, adopt a fixed-point scaled `rewardRate` (e.g., multiply numerator by `1e18` before dividing, then divide again when computing `earned()`), which is the standard Synthetix pattern and eliminates per-epoch dust entirely.

### Proof of Concept
1. Admin calls `notifyRewardAmount(1_000_001)` with `duration = 604_800` (7 days).
2. `rewardRate = 1_000_001 / 604_800 = 1` (truncated).
3. Total distributed over the full period: `1 * 604_800 = 604_800` tokens.
4. Tokens permanently stuck: `1_000_001 - 604_800 = 395_201` tokens.
5. No function in `KernelDepositPool` can retrieve these tokens.
6. On the next `notifyRewardAmount()` call, the same truncation recurs, compounding the loss. [3](#0-2)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L566-592)
```text
    function notifyRewardAmount(uint256 _amount) external onlyRole(DEFAULT_ADMIN_ROLE) updateReward(address(0)) {
        if (_amount == 0) revert AmountZero();

        // Prevent starting a reward period when no tokens are staked to avoid unallocated rewards
        if (totalKernelStaked == 0) revert NoStakedTokens();

        // Transfer reward tokens into the contract
        uint256 balanceBefore = rewardsToken.balanceOf(address(this));
        rewardsToken.safeTransferFrom(msg.sender, address(this), _amount);
        uint256 balanceAfter = rewardsToken.balanceOf(address(this));
        // Calculate the actual amount of tokens received in case of a transfer fee (tax)
        uint256 receivedAmount = balanceAfter - balanceBefore;

        if (block.timestamp >= finishAt) {
            rewardRate = receivedAmount / duration;
        } else {
            uint256 remaining = (finishAt - block.timestamp) * rewardRate;
            rewardRate = (receivedAmount + remaining) / duration;
        }

        if (rewardRate == 0) revert RewardRateZero();

        finishAt = block.timestamp + duration;
        updatedAt = block.timestamp;

        emit NotifyRewardAmount(receivedAmount, finishAt);
    }
```
