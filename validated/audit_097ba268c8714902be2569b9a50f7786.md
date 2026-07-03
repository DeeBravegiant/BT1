Audit Report

## Title
Permanent Freezing of Unclaimed Yield When Last Staker Exits During Active Reward Period - (File: `contracts/KERNEL/KernelDepositPool.sol`)

## Summary
When the last staker calls `initiateWithdrawal` for their full balance during an active reward period, `totalKernelStaked` drops to zero. `rewardPerToken()` then permanently returns the frozen `rewardPerTokenStored` value, and all reward tokens allocated for the remaining period (`rewardRate * (finishAt - block.timestamp)`) are irrecoverably locked in the contract. No admin sweep or recovery function exists.

## Finding Description
The `initiateWithdrawal` function applies `updateReward(msg.sender)` before its body, correctly snapshotting the staker's accrued rewards while `totalKernelStaked` is still non-zero. [1](#0-0) 

The function body then decrements both `balanceOf` and `totalKernelStaked`. If this is the last staker, `totalKernelStaked` becomes zero. [2](#0-1) 

`rewardPerToken()` short-circuits to return the frozen `rewardPerTokenStored` whenever `totalKernelStaked == 0`, even though `rewardRate` is still non-zero and `finishAt` is still in the future. [3](#0-2) 

The only guard against this scenario is the check in `notifyRewardAmount` that reverts if `totalKernelStaked == 0` at the time of reward notification. [4](#0-3)  This prevents starting a period with no stakers, but does not prevent stakers from exiting after a period has started.

The contract's NatSpec explicitly acknowledges this risk and states it is mitigated solely by an off-chain operational promise to keep tokens staked. [5](#0-4)  There is no `recoverERC20`, `sweepStrandedRewards`, or any admin function in the contract to recover stranded reward tokens. [6](#0-5) 

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.** The staker correctly receives their earned rewards up to the withdrawal moment via `getReward()`. However, the reward tokens allocated for the remainder of the active period — `rewardRate * (finishAt - block.timestamp)` — are permanently locked in the contract with no on-chain recovery path. This matches the allowed impact class "Medium. Permanent freezing of unclaimed yield."

## Likelihood Explanation
Any unprivileged staker can trigger this unilaterally by calling `initiateWithdrawal` for their full balance. No special role, collusion, or privileged access is required. It can occur accidentally (a staker simply wanting to exit) or deliberately (griefing). The sole mitigation is an off-chain operational promise with zero on-chain enforcement. Likelihood is **Medium**: requires the last staker to exit mid-period, a plausible real-world scenario.

## Recommendation
1. Add an admin `recoverERC20` or `sweepStrandedRewards` function callable only after `finishAt`, restricted to the surplus reward balance (i.e., `rewardsToken.balanceOf(address(this))` minus any pending user `rewards` balances).
2. Alternatively, enforce on-chain in `initiateWithdrawal` that the call cannot reduce `totalKernelStaked` to zero while `block.timestamp < finishAt`.
3. At minimum, add a `rescueRewards` function callable by `DEFAULT_ADMIN_ROLE` after the reward period ends to recover any unallocated reward tokens.

## Proof of Concept
```solidity
// 1. Admin sets duration and notifies reward amount (1000e18 tokens, 10-day period)
//    totalKernelStaked > 0 at this point (passes NoStakedTokens check)
pool.setRewardsDuration(10 days);
rewardToken.approve(address(pool), 1000e18);
pool.notifyRewardAmount(1000e18);

// 2. Warp halfway through the period
vm.warp(block.timestamp + 5 days);

// 3. Last staker initiates withdrawal for full balance
// updateReward runs first: correctly credits earned rewards up to now
pool.initiateWithdrawal(stakerBalance);
// totalKernelStaked is now 0

// 4. Staker claims their correctly-accrued rewards (works fine)
pool.getReward();

// 5. Warp to end of period
vm.warp(finishAt);

// 6. Assert: ~500e18 reward tokens are permanently locked
// rewardPerToken() returns frozen rewardPerTokenStored forever
// No staker can claim them; no admin can recover them
assertGt(rewardToken.balanceOf(address(pool)), 0);
// Verify no recovery function exists — all admin functions enumerated in L544-621
// confirm there is no sweep/rescue/recover mechanism
```

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L17-23)
```text
 * @dev Modified from https://github.com/Synthetixio/synthetix/blob/develop/contracts/StakingRewards.sol
 * @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
 *      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
 *      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
 *      as well as for the entire duration of the reward period. Otherwise, staking just 1 wei by any address
 *      will ensure that the contract never has any unallocated rewards.
 */
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-320)
```text
    function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L325-326)
```text
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L544-621)
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
}
```
