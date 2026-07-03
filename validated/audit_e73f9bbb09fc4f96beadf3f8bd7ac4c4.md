### Title
Permanent Loss of Reward Tokens Due to Integer Division Truncation in `notifyRewardAmount` - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
In `KernelDepositPool.notifyRewardAmount`, the `rewardRate` is computed via integer division `receivedAmount / duration`. The remainder (`receivedAmount % duration`) is permanently locked in the contract with no recovery path, causing a structural, cumulative loss of reward tokens every reward period.

### Finding Description
In `notifyRewardAmount`, the reward rate is set as:

```solidity
if (block.timestamp >= finishAt) {
    rewardRate = receivedAmount / duration;
} else {
    uint256 remaining = (finishAt - block.timestamp) * rewardRate;
    rewardRate = (receivedAmount + remaining) / duration;
}
``` [1](#0-0) 

Solidity integer division truncates toward zero. The quantity `receivedAmount % duration` (up to `duration - 1` wei) is transferred into the contract but never accounted for in `rewardRate`. Because `rewardRate * duration < receivedAmount`, those tokens are permanently stranded: the contract has no sweep, rescue, or recovery function for excess reward token balance.

The same truncation recurs in the mid-period rollover branch: `(receivedAmount + remaining) % duration` is also silently discarded. [2](#0-1) 

### Impact Explanation
Every call to `notifyRewardAmount` permanently freezes up to `duration - 1` wei of the reward token inside the contract. Over multiple reward periods this accumulates. There is no admin function, sweep, or rescue path to recover these tokens. The impact classification is **Medium — Permanent freezing of unclaimed yield**.

Concrete magnitude: if `duration = 30 days = 2,592,000 seconds`, up to `2,591,999` wei of the reward token is lost per call. For a reward token with low decimals (e.g., 6-decimal USDC), this is up to ~2.59 USDC per `notifyRewardAmount` call, compounding across every reward epoch.

### Likelihood Explanation
This is triggered unconditionally on every legitimate admin call to `notifyRewardAmount`. No special conditions, attacker action, or timing is required. Likelihood is **High** — it fires every reward period by design.

### Recommendation
Track the undistributed dust and either:
1. Carry it forward into the next reward period by adding it to `receivedAmount` in the next `notifyRewardAmount` call (requires storing the leftover), or
2. Add an admin-callable `recoverExcessRewards` function that computes `rewardsToken.balanceOf(address(this)) - (rewardRate * (finishAt - block.timestamp))` and transfers the difference to a treasury address.

### Proof of Concept
```
Setup:
  duration = 7 days = 604,800 seconds
  Admin calls notifyRewardAmount with receivedAmount = 1,000,000 wei

Execution:
  rewardRate = 1,000,000 / 604,800 = 1 (integer division)
  Tokens distributed over period = 1 * 604,800 = 604,800 wei
  Tokens permanently locked = 1,000,000 - 604,800 = 395,200 wei (39.5% of the reward)

Over 10 reward periods with the same parameters:
  Total locked = 3,952,000 wei (39.5% of all rewards ever deposited)
```

The locked tokens sit in the contract's `rewardsToken` balance indefinitely, indistinguishable from legitimately owed rewards, and are unreachable by any on-chain path. [3](#0-2)

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
