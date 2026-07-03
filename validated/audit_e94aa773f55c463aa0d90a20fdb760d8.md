### Title
Reward Tokens Permanently Locked When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool` contains a Synthetix-style staking rewards mechanism. If `totalKernelStaked` reaches zero at any point during an active reward distribution window, all reward tokens emitted during that zero-staked interval are permanently locked in the contract with no recovery path. The contract itself acknowledges this in its NatSpec but relies entirely on off-chain operational controls rather than any on-chain safeguard.

### Finding Description
`KernelDepositPool.notifyRewardAmount` sets a `rewardRate` and a `finishAt` deadline, transferring reward tokens into the contract for the full duration. [1](#0-0) 

During the reward period, any staker may call `initiateWithdrawal`, which immediately decrements `totalKernelStaked`. [2](#0-1) 

`rewardPerToken()` returns the frozen `rewardPerTokenStored` whenever `totalKernelStaked == 0`, meaning the reward rate continues to tick but no address accumulates any share of those emissions. [3](#0-2) 

The contract has no `recoverTokens`, `withdrawExcessRewards`, or any other admin function capable of retrieving reward tokens that were never attributed to any staker. The entire admin surface is limited to `setRewardsDuration`, `notifyRewardAmount`, `setWithdrawalDelay`, and `setMaxNumberOfWithdrawalsPerUser`. [4](#0-3) 

The contract's own NatSpec explicitly acknowledges this gap and defers to operational controls: [5](#0-4) 

When a subsequent reward period is started, `notifyRewardAmount` computes `remaining = (finishAt - block.timestamp) * rewardRate`, which reflects the rate-based projection of undistributed tokens — but the actual token balance in the contract already includes the silently lost tokens from the zero-staked interval. Those tokens are never rolled forward and remain permanently irrecoverable. [6](#0-5) 

### Impact Explanation
**Medium — Permanent freezing of unclaimed yield.**

Reward tokens transferred into the contract for a distribution period that includes any zero-staked interval are permanently locked. They cannot be claimed by any user (no staker was present to earn them) and cannot be recovered by the admin (no recovery function exists). The magnitude scales with the duration of the zero-staked gap and the `rewardRate`.

### Likelihood Explanation
The scenario is realistic and reachable without any privileged action:

- A single large staker holding the majority of `totalKernelStaked` can call `initiateWithdrawal` at any time during an active reward period, driving `totalKernelStaked` to zero.
- Market conditions (e.g., a sharp drop in KERNEL price, a competing yield opportunity) can cause coordinated exits.
- The withdrawal delay (`withdrawalDelay`, up to 30 days) does not prevent `totalKernelStaked` from dropping to zero — it only delays the final token transfer; the balance decrement happens immediately in `initiateWithdrawal`. [7](#0-6) 

The `notifyRewardAmount` guard (`if (totalKernelStaked == 0) revert NoStakedTokens()`) only prevents starting a new campaign with zero stakers; it does nothing to protect rewards already in flight. [8](#0-7) 

### Recommendation
Add an admin-callable recovery function that computes the difference between the contract's actual `rewardsToken` balance and the sum of all legitimately owed rewards (i.e., `rewardRate * remainingDuration + sum(rewards[user])`), and transfers the surplus to a designated treasury address. Alternatively, adopt the pattern from the Origin Protocol fix (PR #688): when `totalKernelStaked` drops to zero mid-period, snapshot the undistributed amount and allow the admin to reclaim it or roll it into the next campaign explicitly.

### Proof of Concept

1. Admin calls `notifyRewardAmount(1_000_000e18)` with `duration = 30 days`. `rewardRate = 1_000_000e18 / 30 days ≈ 385e18 per second`. Contract holds `1_000_000e18` reward tokens.

2. Alice is the only staker with `balanceOf[Alice] = 1000e18`, `totalKernelStaked = 1000e18`.

3. On day 1, Alice calls `initiateWithdrawal(1000e18)`. `totalKernelStaked` becomes `0` immediately. [7](#0-6) 

4. For the remaining 29 days, every call to `rewardPerToken()` returns the frozen `rewardPerTokenStored` because `totalKernelStaked == 0`. Approximately `385e18 * 29 days ≈ 966_000e18` reward tokens are emitted by the rate but attributed to nobody. [9](#0-8) 

5. After `finishAt`, the contract holds ≈`966_000e18` reward tokens that no address can claim. There is no function to retrieve them. They are permanently locked.

6. If the admin attempts to start a new campaign, `notifyRewardAmount` reverts with `NoStakedTokens` if `totalKernelStaked` is still zero, or proceeds with a new `rewardRate` that does not account for the stranded tokens — they remain locked regardless. [1](#0-0)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L17-22)
```text
 * @dev Modified from https://github.com/Synthetixio/synthetix/blob/develop/contracts/StakingRewards.sol
 * @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
 *      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
 *      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
 *      as well as for the entire duration of the reward period. Otherwise, staking just 1 wei by any address
 *      will ensure that the contract never has any unallocated rewards.
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L408-413)
```text
    function rewardPerToken() public view returns (uint256) {
        if (totalKernelStaked == 0) {
            return rewardPerTokenStored;
        }
        return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
            / totalKernelStaked;
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
