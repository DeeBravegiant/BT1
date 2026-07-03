### Title
Reward Tokens Permanently Locked When `totalKernelStaked` Drops to Zero Mid-Period - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
In `KernelDepositPool`, when all stakers call `initiateWithdrawal()` during an active reward distribution window, `totalKernelStaked` drops to zero. The `rewardPerToken()` function returns `rewardPerTokenStored` unchanged for the entire zero-staked interval, meaning the `rewardRate × elapsed_time` worth of reward tokens that were deposited via `notifyRewardAmount()` are permanently locked in the contract with no recovery path.

### Finding Description

`initiateWithdrawal()` immediately decrements `totalKernelStaked` at the point of initiation, not at claim time: [1](#0-0) 

When `totalKernelStaked` reaches zero, `rewardPerToken()` short-circuits and returns the stored value unchanged: [2](#0-1) 

The `updateReward` modifier always advances `updatedAt` to `lastTimeRewardApplicable()`: [3](#0-2) 

So when the next user eventually stakes, `updatedAt` is refreshed to the current timestamp, silently skipping the entire zero-staked interval. The `rewardRate × (duration_of_zero_staked_period)` worth of reward tokens remain in the contract balance but are never attributed to any staker and can never be claimed. There is no admin `recoverERC20` or equivalent function in the contract: [4](#0-3) 

The contract's own NatSpec explicitly acknowledges this risk but relies on an off-chain operational assumption rather than a code-level guarantee: [5](#0-4) 

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

Reward tokens transferred into the contract via `notifyRewardAmount()` for the zero-staked interval are irrecoverably locked. No staker can claim them, and no admin function exists to rescue them. The magnitude equals `rewardRate × T_zero` where `T_zero` is the duration during which `totalKernelStaked == 0` within the active reward window (up to the full `duration`, e.g., weeks of rewards).

### Likelihood Explanation

Any KERNEL staker can trigger this by calling `initiateWithdrawal()` for their full balance. If they are the last (or only) staker, `totalKernelStaked` immediately drops to zero. This is a normal, permissionless user action with no special role required. The scenario is realistic whenever staker count is low (e.g., early protocol life or after a market event causes mass exits).

### Recommendation

Add a rescue function callable by the admin to recover reward tokens that were not distributed due to a zero-staked period:

```solidity
function recoverUndistributedRewards(address recipient) external onlyRole(DEFAULT_ADMIN_ROLE) {
    require(block.timestamp > finishAt, "Reward period not finished");
    uint256 undistributed = rewardsToken.balanceOf(address(this));
    rewardsToken.safeTransfer(recipient, undistributed);
}
```

Alternatively, track the amount of rewards that were "skipped" during zero-staked intervals and allow them to be rolled into the next reward period via `notifyRewardAmount`.

### Proof of Concept

1. Admin calls `notifyRewardAmount(1_000_000e18)` with `duration = 30 days`. `rewardRate = 1_000_000e18 / 30 days`.
2. Alice is the only staker with `balanceOf[Alice] = 1000e18`, `totalKernelStaked = 1000e18`.
3. After 1 day, Alice calls `initiateWithdrawal(1000e18)`. `totalKernelStaked` becomes `0` immediately.
4. 29 days pass. `rewardPerToken()` returns `rewardPerTokenStored` unchanged for all 29 days.
5. Bob stakes 1 wei. `updateReward(Bob)` fires: `updatedAt` advances to `finishAt`, skipping 29 days of rewards.
6. `rewardRate × 29 days ≈ 966,667e18` reward tokens remain in the contract forever — no staker can claim them, no admin can recover them. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L18-22)
```text
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
