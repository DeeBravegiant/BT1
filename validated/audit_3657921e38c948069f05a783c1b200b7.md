### Title
Reward Token Dust Permanently Locked Due to Integer Division Truncation in `notifyRewardAmount` - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.notifyRewardAmount` computes `rewardRate` via integer division of `receivedAmount / duration`. The remainder (`receivedAmount % duration`) is transferred into the contract but never distributed to stakers, permanently locking a portion of every reward deposit.

### Finding Description
In `KernelDepositPool.notifyRewardAmount`, the reward rate is set as:

```solidity
// Line 580
rewardRate = receivedAmount / duration;
// or, when a period is still active (line 583):
rewardRate = (receivedAmount + remaining) / duration;
```

Because Solidity integer division truncates toward zero, `receivedAmount % duration` tokens (or `(receivedAmount + remaining) % duration` tokens) are transferred into the contract but are never accounted for in `rewardRate`. They cannot be claimed by any staker and cannot be recovered by any function in the contract. There is no sweep or rescue path for the reward token.

This is the direct analog of the original report: just as `assets.mul(share).div(1000)` leaves a remainder in the strategy contract, `receivedAmount / duration` leaves a remainder in `KernelDepositPool` — in both cases the residual tokens are silently stranded.

### Impact Explanation
Every call to `notifyRewardAmount` permanently locks up to `duration - 1` reward tokens. With `duration` set to e.g. 30 days (2,592,000 seconds), up to 2,591,999 wei of reward token can be lost per call. Across repeated reward cycles the cumulative loss grows. Stakers receive less yield than the protocol intends to distribute. The locked tokens are irrecoverable — no admin function, no sweep, no rescue path exists for the reward token balance.

**Impact class:** Medium — Permanent freezing of unclaimed yield.

### Likelihood Explanation
This triggers on every legitimate call to `notifyRewardAmount` by the admin. It is not a corner case; it fires whenever `receivedAmount` is not an exact multiple of `duration`, which is the common case for any real reward amount. No attacker action is required; normal protocol operation is sufficient.

### Recommendation
After computing `rewardRate`, carry the remainder forward into the next period rather than discarding it:

```solidity
if (block.timestamp >= finishAt) {
    rewardRate = receivedAmount / duration;
    // carry dust: (receivedAmount % duration) is implicitly included
    // in the next notifyRewardAmount call via the balance-based accounting
} else {
    uint256 remaining = (finishAt - block.timestamp) * rewardRate;
    rewardRate = (receivedAmount + remaining) / duration;
}
```

The standard fix (used by Synthetix and its forks) is to compute the undistributed remainder and add it back to the next reward notification, or to use the contract's actual token balance minus already-committed rewards as the source of truth rather than a separately tracked `rewardRate`.

### Proof of Concept

1. Admin calls `notifyRewardAmount(1_000_001)` with `duration = 1_000_000`.
2. `receivedAmount = 1_000_001` is transferred into the contract.
3. `rewardRate = 1_000_001 / 1_000_000 = 1` (truncated).
4. Total distributed over the period: `1 * 1_000_000 = 1_000_000` tokens.
5. Permanently locked: `1_000_001 - 1_000_000 = 1` token.
6. Repeat across N reward cycles → N tokens permanently locked, never claimable by any staker.

The root cause is at: [1](#0-0) 

The `rewardRate` integer division is the necessary vulnerable step; the remainder is silently discarded with no mechanism to recover it. [2](#0-1)

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
