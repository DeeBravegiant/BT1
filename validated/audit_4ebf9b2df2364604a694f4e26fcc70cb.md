Audit Report

## Title
Reward Tokens Permanently Locked When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`KernelDepositPool` uses a Synthetix-style staking rewards model where the `updateReward` modifier unconditionally advances `updatedAt` to `lastTimeRewardApplicable()` even when `totalKernelStaked == 0`. Because `rewardPerToken()` short-circuits and returns `rewardPerTokenStored` unchanged when no tokens are staked, any reward tokens accrued during a zero-staked interval are silently discarded and permanently locked in the contract with no recovery path. The contract's own NatSpec acknowledges this behavior and relies solely on operational controls that are not enforced in code.

## Finding Description
The `updateReward` modifier unconditionally advances `updatedAt`:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();   // returns unchanged value when totalKernelStaked == 0
    updatedAt = lastTimeRewardApplicable();    // ALWAYS advances, consuming the time window
    ...
}
``` [1](#0-0) 

`rewardPerToken()` short-circuits when `totalKernelStaked == 0`, returning the stored value without accumulating any new rewards:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;   // no increase — rewards for this interval are skipped
    }
    ...
}
``` [2](#0-1) 

`initiateWithdrawal` unconditionally subtracts from `totalKernelStaked` with no floor check, allowing it to reach zero: [3](#0-2) 

The `notifyRewardAmount` guard (`if (totalKernelStaked == 0) revert NoStakedTokens()`) only prevents *starting* a reward period with zero stakers. It does not prevent all stakers from withdrawing *during* an active reward period, which is the actual attack path. [4](#0-3) 

There are no admin rescue, sweep, or recovery functions. The only admin functions are `setRewardsDuration`, `notifyRewardAmount`, `setWithdrawalDelay`, and `setMaxNumberOfWithdrawalsPerUser`. [5](#0-4) 

The contract's own NatSpec explicitly acknowledges this limitation and relies on operational controls ("ensuring there are always some tokens staked") rather than code enforcement: [6](#0-5) 

## Impact Explanation
Reward tokens (`rewardsToken`) deposited by the admin via `notifyRewardAmount` are permanently locked in `KernelDepositPool` for any time interval during which `totalKernelStaked == 0`. The `rewardRate * Δt` tokens that should have accrued during that interval are never distributed to any user and cannot be recovered by any on-chain mechanism. This constitutes **permanent freezing of unclaimed yield** (Medium severity per the allowed impact scope).

## Likelihood Explanation
Any scenario where all stakers exit during an active reward period triggers the bug. No privileged role needs to be compromised — the admin legitimately starts a reward period, and users legitimately call `initiateWithdrawal`. The withdrawal delay (`withdrawalDelay`, up to `MAX_WITHDRAWAL_DELAY = 30 days`) means `totalKernelStaked` drops to zero at `initiateWithdrawal` time, not at `claimWithdrawal` time, so the zero-staked window can span the entire delay period. A single large staker withdrawing their full balance during a reward window is a normal protocol event. [7](#0-6) 

## Recommendation
In the `updateReward` modifier, only advance `updatedAt` when there are stakers to absorb rewards:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    if (totalKernelStaked > 0) {
        updatedAt = lastTimeRewardApplicable();
    }
    if (_account != address(0)) {
        rewards[_account] = earned(_account);
        userRewardPerTokenPaid[_account] = rewardPerTokenStored;
    }
    _;
}
```

This preserves the unallocated rewards for future stakers rather than silently discarding them.

## Proof of Concept
1. Admin calls `notifyRewardAmount(1_000e18)` while one staker has 100 KERNEL staked → `rewardRate = 1_000e18 / duration`, `finishAt = now + duration`, `updatedAt = now`.
2. The staker calls `initiateWithdrawal(100)` → `updateReward` fires (staker's accrued rewards checkpointed correctly), `totalKernelStaked = 0`, `updatedAt = now`.
3. 30 days pass (`withdrawalDelay`). During this window `totalKernelStaked == 0` and `block.timestamp < finishAt`. No `updateReward` calls occur.
4. The staker calls `claimWithdrawal` (no `updateReward` — safe). Then calls `stake(1)` → `updateReward` fires: `rewardPerToken()` returns `rewardPerTokenStored` (unchanged), but `updatedAt` jumps to `now`. The 30-day reward window is consumed with zero distribution.
5. The reward tokens for those 30 days (`rewardRate * 30 days`) remain in the contract balance permanently, with no mechanism to recover them.

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L35-35)
```text
    uint256 public constant MAX_WITHDRAWAL_DELAY = 30 days;
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-327)
```text
    function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
        if (_amount == 0) revert AmountZero();
        if (balanceOf[msg.sender] < _amount) revert InsufficientStakedBalance();
        if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();

        balanceOf[msg.sender] -= _amount;
        totalKernelStaked -= _amount;

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L544-620)
```text
    /*//////////////////////////////////////////////////////////////
                            ADMIN FUNCTIONS
    //////////////////////////////////////////////////////////////*/

    /**
     * @notice Sets the duration for rewards distribution
     * @param _duration The duration in seconds
     */
    function setRewardsDuration(uint256 _duration) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (finishAt >= block.timestamp) revert RewardDurationNotFinished();
        if (_duration == 0) revert InvalidDuration();
        duration = _duration;
        emit RewardsDurationUpdated(_duration);
    }

    /**
     * @notice Notifies the contract about a new reward amount
     * @dev Uses a transfer-in pattern to determine the exact reward amount received.
     *      Also, to avoid undistributed rewards when no one is staked, this function reverts if totalKernelStaked is
     *      zero.
     * @param _amount The amount of reward tokens to add
     */
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

    /**
     * @notice Updates the withdrawal delay
     * @param _withdrawalDelay The new withdrawal delay
     */
    function setWithdrawalDelay(uint256 _withdrawalDelay) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_withdrawalDelay == 0) revert InvalidWithdrawalDelay();
        if (_withdrawalDelay > MAX_WITHDRAWAL_DELAY) revert MaximumWithdrawalDelayExceeded();

        withdrawalDelay = _withdrawalDelay;
        emit WithdrawalDelayUpdated(_withdrawalDelay);
    }

    /**
     * @notice Updates the maximum number of withdrawals per user
     * @param _maxNumberOfWithdrawalsPerUser The new maximum number of withdrawals per user
     */
    function setMaxNumberOfWithdrawalsPerUser(uint256 _maxNumberOfWithdrawalsPerUser)
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        if (_maxNumberOfWithdrawalsPerUser == 0 || _maxNumberOfWithdrawalsPerUser > MAX_WITHDRAWALS_PER_USER) {
            revert InvalidMaxNumberOfWithdrawalsPerUser();
        }

        maxNumberOfWithdrawalsPerUser = _maxNumberOfWithdrawalsPerUser;
        emit MaxNumberOfWithdrawalsPerUserUpdated(_maxNumberOfWithdrawalsPerUser);
    }
```
