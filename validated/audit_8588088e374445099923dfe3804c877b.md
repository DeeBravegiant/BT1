### Title
Reward Tokens Permanently Locked When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool` distributes reward tokens over a fixed period set by `notifyRewardAmount`. If all stakers withdraw their KERNEL tokens after a reward period has started, `totalKernelStaked` drops to zero. During any interval where `totalKernelStaked == 0`, the reward accumulation logic freezes and the reward tokens allocated to that interval are permanently locked in the contract with no recovery path.

### Finding Description
`notifyRewardAmount` transfers reward tokens into the contract and sets a `rewardRate` for the duration. It guards against starting a period with zero stakers: [1](#0-0) 

However, this guard only applies at the moment `notifyRewardAmount` is called. After the reward period starts, any staker can call `initiateWithdrawal` followed by `claimWithdrawal` to fully exit. If the last staker exits, `totalKernelStaked` becomes zero.

The `rewardPerToken()` function short-circuits when `totalKernelStaked == 0`, returning the frozen `rewardPerTokenStored` without advancing it: [2](#0-1) 

The `updateReward` modifier, which is the only place `updatedAt` is refreshed, runs on every user action. When a new staker eventually arrives and triggers `updateReward`, `rewardPerToken()` still returns the frozen `rewardPerTokenStored` (since `totalKernelStaked` is still 0 at modifier entry), and then `updatedAt` is advanced to `lastTimeRewardApplicable()`: [3](#0-2) 

This silently discards all rewards that were supposed to be distributed during the zero-staking interval — `rewardRate × (gap duration)` worth of tokens — which remain in the contract balance forever. The contract contains no `recoverERC20`, sweep, or any other admin function to retrieve these stranded tokens. The only admin functions are `setRewardsDuration`, `notifyRewardAmount`, `setWithdrawalDelay`, and `setMaxNumberOfWithdrawalsPerUser`. [4](#0-3) 

The contract's own NatSpec acknowledges this: *"If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards for that period will stay locked in the contract."* — but provides no on-chain remedy. [5](#0-4) 

### Impact Explanation
Reward tokens deposited by the admin via `notifyRewardAmount` are permanently frozen in the contract for any time interval during which `totalKernelStaked == 0`. These tokens cannot be claimed by any user and cannot be recovered by the admin. This constitutes **permanent freezing of unclaimed yield** (Medium severity per the allowed impact scope).

### Likelihood Explanation
Any staker can call `initiateWithdrawal` at any time after staking. If the last remaining staker exits during an active reward window — a normal, permissionless user action — the remaining rewards are locked. This requires no privileged access, no front-running, and no coordination beyond ordinary withdrawal behavior. The likelihood is medium: it depends on all stakers choosing to exit simultaneously, which is plausible during market stress or protocol migration events.

### Recommendation
Add an admin-accessible token recovery function that can only sweep the difference between the contract's reward token balance and the total of all currently earned (but unclaimed) rewards. Alternatively, track "unallocated" reward tokens explicitly and allow the admin to reclaim them after the reward period ends:

```solidity
function recoverUnallocatedRewards(address recipient) external onlyRole(DEFAULT_ADMIN_ROLE) {
    require(block.timestamp > finishAt, "Reward period not finished");
    uint256 unallocated = rewardsToken.balanceOf(address(this)) - totalEarnedButUnclaimed();
    rewardsToken.safeTransfer(recipient, unallocated);
}
```

A simpler alternative is to call `notifyRewardAmount` with the leftover balance immediately after the period ends, effectively rolling unallocated rewards into the next period.

### Proof of Concept

1. Admin calls `setRewardsDuration(7 days)`.
2. Alice stakes 100 KERNEL → `totalKernelStaked = 100`.
3. Admin calls `notifyRewardAmount(1000e18)` → `rewardRate = 1000e18 / 7 days`, `finishAt = now + 7 days`.
4. After 1 day, Alice calls `initiateWithdrawal(100)` → `totalKernelStaked = 0`.
5. After `withdrawalDelay`, Alice calls `claimWithdrawal` and receives her 100 KERNEL back.
6. Alice calls `getReward()` and receives ~142e18 reward tokens (1 day worth).
7. The remaining ~857e18 reward tokens (6 days worth) sit in the contract.
8. No function exists to recover them. `rewardPerTokenStored` is frozen. Any future staker who joins before `finishAt` will earn rewards only from the moment they stake, but the 6-day gap's allocation is irrecoverable. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L344-379)
```text
    function claimWithdrawal(uint256 _withdrawalId) external nonReentrant {
        Withdrawal storage withdrawal = withdrawals[_withdrawalId];

        if (withdrawal.user == address(0)) {
            revert WithdrawalDoesNotExist();
        }

        if (withdrawal.user != msg.sender) {
            revert NotYourWithdrawal();
        }

        if (block.timestamp < withdrawal.unlockTime) {
            revert WithdrawalNotReady();
        }

        if (withdrawal.claimed) {
            revert WithdrawalAlreadyClaimed();
        }

        withdrawal.claimed = true;

        // Remove the withdrawal ID from the user's list of withdrawal IDs
        uint256[] storage userWithdrawalIdsArray = userWithdrawalIds[msg.sender];
        for (uint256 i = 0; i < userWithdrawalIdsArray.length; ++i) {
            if (userWithdrawalIdsArray[i] == _withdrawalId) {
                userWithdrawalIdsArray[i] = userWithdrawalIdsArray[userWithdrawalIdsArray.length - 1];
                userWithdrawalIdsArray.pop();
                break;
            }
        }

        // Transfer the KERNEL tokens from contract to user
        kernelToken.safeTransfer(msg.sender, withdrawal.amount);

        emit WithdrawalClaimed(msg.sender, withdrawal.amount, _withdrawalId);
    }
```

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L552-620)
```text
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
