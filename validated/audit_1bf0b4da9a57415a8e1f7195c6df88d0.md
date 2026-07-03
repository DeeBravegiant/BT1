### Title
Reward Tokens Permanently Frozen When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool` uses a Synthetix-style staking rewards model where `rewardPerToken()` returns the stored value unchanged when `totalKernelStaked == 0`. If all stakers withdraw during an active reward window, the `updateReward` modifier still advances `updatedAt` past the zero-staked interval, permanently losing the rewards that accrued during that gap. There is no on-chain recovery mechanism.

### Finding Description
The `rewardPerToken()` function short-circuits to `rewardPerTokenStored` when `totalKernelStaked == 0`:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;   // rewards stop accumulating
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

The `updateReward` modifier, which is applied to every state-changing function (`stake`, `initiateWithdrawal`, `getReward`, `notifyRewardAmount`), always updates `updatedAt = lastTimeRewardApplicable()` regardless of whether `totalKernelStaked` is zero:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    updatedAt = lastTimeRewardApplicable();   // advances even when totalKernelStaked == 0
    ...
}
```

When `totalKernelStaked` is zero, `rewardPerTokenStored` stays flat while `updatedAt` advances. When a new staker eventually arrives and triggers `updateReward`, the formula `rewardRate * (lastTimeRewardApplicable() - updatedAt)` uses the new (post-zero) `updatedAt`, so the entire zero-staked interval is silently skipped. The reward tokens for that interval remain in the contract with no function to recover them.

The contract's own NatSpec acknowledges this at lines 18–22 but relies on an off-chain operational assumption ("ensuring there are always some tokens staked") rather than any on-chain enforcement. Any user can call `initiateWithdrawal()` to reduce their stake, and there is no guard preventing `totalKernelStaked` from reaching zero after `notifyRewardAmount` has been called.

### Impact Explanation
Reward tokens (the `rewardsToken` ERC-20) that should have been distributed to stakers are permanently locked in the `KernelDepositPool` contract. There is no admin rescue function, no `recoverERC20`, and no way to restart the reward period to cover the lost interval. The impact is **permanent freezing of unclaimed yield**.

### Likelihood Explanation
The scenario requires `totalKernelStaked` to reach zero during an active reward window. This is realistic when:
- There is a single dominant staker who initiates a full withdrawal.
- A coordinated or panic-driven mass exit occurs (e.g., a depeg event for the KERNEL token).
- The protocol is in an early stage with few stakers.

`initiateWithdrawal()` is permissionless for any staker; no admin action is required to trigger the condition.

### Recommendation
Add an on-chain guard in `initiateWithdrawal()` that prevents `totalKernelStaked` from reaching zero while a reward period is active (`block.timestamp < finishAt`). Alternatively, implement a `recoverStuckRewards()` admin function that can sweep reward tokens that were never distributed (computed as `rewardRate * zero_staked_duration`), analogous to the `stuckEmissionsRecovery` fix recommended in the external report.

### Proof of Concept
1. Admin calls `notifyRewardAmount(1_000e18)` while Alice has 100 KERNEL staked → `rewardRate = 1_000e18 / duration`, `finishAt = now + duration`, `updatedAt = now`.
2. Alice calls `initiateWithdrawal(100)` → `totalKernelStaked = 0`, `updateReward` fires: `rewardPerTokenStored` stays at its current value, `updatedAt = now`.
3. 50% of the reward period elapses with `totalKernelStaked == 0`. Every call to `rewardPerToken()` returns `rewardPerTokenStored` unchanged.
4. Bob stakes 1 KERNEL → `updateReward` fires: `rewardPerTokenStored += rewardRate * (now - updatedAt) / 1` — but `updatedAt` was already advanced to the moment Alice withdrew, so only the remaining ~50% of rewards are captured.
5. The first 50% of reward tokens (`500e18`) are permanently stuck in the contract. No function exists to recover them. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L14-23)
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
 */
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L232-242)
```text
    modifier updateReward(address _account) {
        rewardPerTokenStored = rewardPerToken();
        updatedAt = lastTimeRewardApplicable();

        if (_account != address(0)) {
            rewards[_account] = earned(_account);
            userRewardPerTokenPaid[_account] = rewardPerTokenStored;
        }

        _;
    }
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
