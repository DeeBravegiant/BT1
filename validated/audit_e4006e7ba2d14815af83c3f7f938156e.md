### Title
Reward Tokens Permanently Locked When `totalKernelStaked` Drops to Zero With No Recovery Mechanism - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool` distributes reward tokens over a time-based window set by the admin via `notifyRewardAmount`. When `totalKernelStaked` reaches zero during an active reward window, the `rewardPerToken()` function stops accumulating rewards, and those reward tokens are permanently locked in the contract. No admin or user function exists to recover them.

### Finding Description
The `rewardPerToken()` function short-circuits when `totalKernelStaked == 0`, returning the last stored value without advancing the accumulator:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

The contract's own NatSpec acknowledges this at lines 18–22:

> *If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards for that period will stay locked in the contract.*

The `updateReward` modifier snapshots `updatedAt = lastTimeRewardApplicable()` on every user action. When the last staker calls `initiateWithdrawal`, `totalKernelStaked` drops to zero and `updatedAt` is set to the current timestamp. From that point forward, `rewardPerToken()` always returns `rewardPerTokenStored` unchanged, so the `rewardRate * remainingTime` worth of reward tokens accumulate in the contract balance with no path to distribute or recover them.

Critically, `KernelDepositPool` has **no `withdrawTokens` or equivalent admin rescue function** for the `rewardsToken`. Compare this to `KernelTop100MerkleDistributor`, which does expose `withdrawTokens` for exactly this purpose. The reward tokens sent in via `notifyRewardAmount` are irrecoverably stranded.

### Impact Explanation
**Medium — Permanent freezing of unclaimed yield.**

Any reward tokens corresponding to the time interval `[lastStakerWithdrawal, finishAt]` are permanently locked in the contract. The amount can be substantial: if `rewardRate = R` and the remaining window is `T` seconds, then `R * T` reward tokens are frozen forever. No future call to `notifyRewardAmount` can unlock them; it only starts a fresh window on top of the stranded balance.

### Likelihood Explanation
**Medium.** This is triggered by normal, permissionless user behavior — any staker calling `initiateWithdrawal` followed by `claimWithdrawal`. If the last staker exits before `finishAt`, the condition is met. In a low-TVL or end-of-campaign scenario this is realistic. No admin collusion or key compromise is required.

### Recommendation
1. Add an admin-callable `recoverRewardTokens(uint256 amount)` function (similar to `KernelTop100MerkleDistributor.withdrawTokens`) that can only be called after `block.timestamp >= finishAt`, allowing recovery of undistributed rewards.
2. Alternatively, track `undistributedRewards` explicitly and allow the admin to roll them into the next reward window via `notifyRewardAmount`.

### Proof of Concept

1. Admin calls `setRewardsDuration(7 days)` then `notifyRewardAmount(700_000e18)` → `rewardRate = 100_000e18 / day`, `finishAt = now + 7 days`.
2. Alice stakes 1000 KERNEL tokens; `totalKernelStaked = 1000`.
3. After 1 day, Alice calls `initiateWithdrawal(1000)` → `totalKernelStaked = 0`, `updatedAt = now`.
4. Alice waits `withdrawalDelay` and calls `claimWithdrawal` — she correctly receives her 1000 KERNEL back.
5. For the remaining 6 days, `rewardPerToken()` always returns `rewardPerTokenStored` (unchanged) because `totalKernelStaked == 0`.
6. At `finishAt`, `600_000e18` reward tokens remain in the contract. No function exists to retrieve them. They are permanently locked. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L14-22)
```text
/**
 * @title Kernel Staking Rewards Contract
 * @dev Implements a basic staking mechanism with rewards
 * @dev Modified from https://github.com/Synthetixio/synthetix/blob/develop/contracts/StakingRewards.sol
 * @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
 *      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
 *      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
 *      as well as for the entire duration of the reward period. Otherwise, staking just 1 wei by any address
 *      will ensure that the contract never has any unallocated rewards.
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L232-241)
```text
    modifier updateReward(address _account) {
        rewardPerTokenStored = rewardPerToken();
        updatedAt = lastTimeRewardApplicable();

        if (_account != address(0)) {
            rewards[_account] = earned(_account);
            userRewardPerTokenPaid[_account] = rewardPerTokenStored;
        }

        _;
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-338)
```text
    function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
        if (_amount == 0) revert AmountZero();
        if (balanceOf[msg.sender] < _amount) revert InsufficientStakedBalance();
        if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();

        balanceOf[msg.sender] -= _amount;
        totalKernelStaked -= _amount;

        // Create a withdrawal record
        uint256 withdrawalId = ++withdrawalCounter;
        uint256 unlockTime = block.timestamp + withdrawalDelay;

        withdrawals[withdrawalId] = Withdrawal({
            user: msg.sender, amount: _amount, unlockTime: unlockTime, claimed: false, withdrawalId: withdrawalId
        });
        userWithdrawalIds[msg.sender].push(withdrawalId);

        emit WithdrawalInitiated(msg.sender, _amount, withdrawalId, unlockTime);
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L408-414)
```text
    function rewardPerToken() public view returns (uint256) {
        if (totalKernelStaked == 0) {
            return rewardPerTokenStored;
        }
        return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
            / totalKernelStaked;
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

**File:** contracts/KERNEL/KernelTop100MerkleDistributor.sol (L461-472)
```text
    function withdrawTokens(address _token, uint256 _amount, address _recipient) external onlyOwner {
        UtilLib.checkNonZeroAddress(_token);
        UtilLib.checkNonZeroAddress(_recipient);

        if (_amount == 0) {
            revert ZeroValueProvided();
        }

        IERC20(_token).safeTransfer(_recipient, _amount);

        emit TokensWithdrawn(_token, _amount, _recipient);
    }
```
