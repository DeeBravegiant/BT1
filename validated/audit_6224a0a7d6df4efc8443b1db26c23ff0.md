### Title
Reward Token Dust Permanently Frozen Due to Integer Division Truncation in `notifyRewardAmount` - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.notifyRewardAmount()` computes `rewardRate` via integer division of the received reward amount by `duration`. The truncated remainder (`receivedAmount % duration`) is transferred into the contract but can never be distributed to stakers or recovered, permanently freezing a portion of every reward deposit.

### Finding Description
In `KernelDepositPool.notifyRewardAmount()`, the reward rate is set as:

```solidity
if (block.timestamp >= finishAt) {
    rewardRate = receivedAmount / duration;          // line 580
} else {
    uint256 remaining = (finishAt - block.timestamp) * rewardRate;
    rewardRate = (receivedAmount + remaining) / duration; // line 583
}
``` [1](#0-0) 

Integer division in Solidity truncates toward zero. The total tokens that will ever be distributed to stakers over the full period is `rewardRate * duration`, which equals `floor(receivedAmount / duration) * duration`. The difference — `receivedAmount % duration` tokens — is transferred into the contract at line 574 but is never scheduled for distribution. [2](#0-1) 

The contract contains no `rescue`, `sweep`, `recover`, or emergency-withdrawal function for the `rewardsToken`. A grep of the entire `KernelDepositPool.sol` confirms zero such functions exist. The only outbound path for `rewardsToken` is `getReward()`, which only transfers amounts tracked in `rewards[user]` — amounts that are derived solely from `rewardRate`, never from the truncated remainder. [3](#0-2) 

### Impact Explanation
Every call to `notifyRewardAmount` permanently freezes up to `duration - 1` wei of reward tokens. For a 7-day distribution window (`duration = 604800` seconds), up to 604,799 wei are lost per call. Over repeated reward cycles this accumulates. The frozen tokens are irrecoverable — they sit in the contract balance but are never credited to any staker's `rewards` mapping and cannot be swept by any role. This matches **Medium: Permanent freezing of unclaimed yield**.

### Likelihood Explanation
`notifyRewardAmount` is called by `DEFAULT_ADMIN_ROLE` in the normal course of protocol operation — it is the intended mechanism for funding staking rewards. Every single invocation silently discards `receivedAmount % duration` tokens. No special conditions, attacker action, or misconfiguration is required; the loss occurs automatically on every reward deposit.

### Recommendation
Scale `rewardRate` by a precision multiplier (e.g., `1e18`) and divide it back out when computing `earned()` and `rewardPerToken()`, so the sub-`duration` remainder is preserved in the rate rather than discarded. Alternatively, track the undistributed dust explicitly and roll it into the next `notifyRewardAmount` call:

```solidity
uint256 dust = receivedAmount % duration;
// carry dust forward into next period or emit it as an event for accounting
rewardRate = (receivedAmount - dust) / duration;
```

### Proof of Concept
1. Admin sets `duration = 604800` (7 days).
2. Admin calls `notifyRewardAmount` transferring `1_000_000` wei of reward token.
3. `rewardRate = 1_000_000 / 604800 = 1` (wei/second).
4. Total distributed over 7 days: `1 * 604800 = 604800` wei.
5. Permanently frozen: `1_000_000 - 604800 = 395200` wei — 39.5% of the reward deposit is irrecoverable.
6. Calling `notifyRewardAmount` again with a new period does not recover the prior dust; it only compounds the loss. [4](#0-3)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L382-390)
```text
    function getReward() external nonReentrant updateReward(msg.sender) {
        uint256 rewardAmount = rewards[msg.sender];

        if (rewardAmount > 0) {
            rewards[msg.sender] = 0;
            rewardsToken.safeTransfer(msg.sender, rewardAmount);
            emit RewardsClaimed(msg.sender, rewardAmount);
        }
    }
```

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
