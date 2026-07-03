### Title
Rewards Permanently Locked When `totalKernelStaked` Drops to Zero During an Active Reward Period — (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool` implements a Synthetix-style staking rewards mechanism. When all stakers withdraw during an active reward distribution window, `totalKernelStaked` reaches zero. The `rewardPerToken()` function returns the stored value unchanged in this state, while the `updateReward` modifier still advances `updatedAt` to the current timestamp. This causes all rewards that accrued during the zero-staking gap to be permanently locked in the contract with no recovery path.

### Finding Description
The `rewardPerToken()` function short-circuits when `totalKernelStaked == 0`:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

The `updateReward` modifier, however, unconditionally advances `updatedAt`:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    updatedAt = lastTimeRewardApplicable();
    ...
}
```

When the last staker calls `initiateWithdrawal`, `updateReward` fires: `rewardPerTokenStored` is captured at the current value and `updatedAt` is set to the current timestamp. After `totalKernelStaked` becomes zero, `rewardRate` continues to tick but `rewardPerTokenStored` never increases. When the next staker arrives and triggers `updateReward`, `updatedAt` is again advanced to the current time — the entire zero-staking gap is silently skipped. The reward tokens that correspond to `rewardRate × gap_duration` remain in the contract balance but are never credited to any user and cannot be recovered, because `notifyRewardAmount` recalculates `rewardRate` only from newly deposited tokens:

```solidity
if (block.timestamp >= finishAt) {
    rewardRate = receivedAmount / duration;
} else {
    uint256 remaining = (finishAt - block.timestamp) * rewardRate;
    rewardRate = (receivedAmount + remaining) / duration;
}
```

The stranded rewards from the zero-staking window are not folded back into any future `rewardRate`.

The contract's own NatSpec acknowledges this:

> "If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards for that period will stay locked in the contract." [1](#0-0) 

The mitigation described ("ensuring there are always some tokens staked") is a deployment-level convention, not an on-chain enforcement. No code prevents `totalKernelStaked` from reaching zero mid-period. [2](#0-1) [3](#0-2) 

### Impact Explanation
**Medium — Permanent freezing of unclaimed yield.**

Reward tokens sent to the contract via `notifyRewardAmount` become permanently unrecoverable if `totalKernelStaked` drops to zero at any point during the reward window. There is no `rescueTokens`, no admin sweep, and no mechanism to fold stranded rewards back into a future period. The loss is proportional to `rewardRate × zero_staking_duration`.

### Likelihood Explanation
**Low-Medium.** The scenario requires all stakers to withdraw during an active reward period. This can occur organically (low participation, a single large staker exits) or can be deliberately triggered by an attacker who is the last remaining staker. The `notifyRewardAmount` guard (`if (totalKernelStaked == 0) revert NoStakedTokens()`) only prevents starting a new period with zero stakers; it does not prevent stakers from draining to zero mid-period. [4](#0-3) 

### Recommendation
1. **On-chain minimum stake enforcement**: Revert `initiateWithdrawal` if the withdrawal would reduce `totalKernelStaked` to zero while `block.timestamp < finishAt`.
2. **Stranded reward recovery**: Track the reward amount that accrued during zero-staking windows and fold it into the next `notifyRewardAmount` call, or allow an admin to sweep it to a treasury.
3. **Extend the reward period**: When `totalKernelStaked` drops to zero, pause the reward clock (`updatedAt` should not advance) so that rewards are preserved for future stakers rather than silently discarded.

### Proof of Concept

1. Admin calls `notifyRewardAmount(7_000e18)` with `duration = 7 days` and `rewardRate = 1_000e18 / day`. One user (Alice) has staked 100 KERNEL.
2. After exactly 1 day, Alice calls `initiateWithdrawal(100)`. `updateReward` fires: `rewardPerTokenStored` is updated to reflect 1 day of rewards; `updatedAt` is set to `block.timestamp` (day 1); `totalKernelStaked` becomes 0.
3. Days 2–7 pass with no stakers. `rewardRate` continues to tick but `rewardPerTokenStored` is frozen.
4. On day 7, Bob calls `stake(1)`. `updateReward` fires: `rewardPerToken()` returns `rewardPerTokenStored` (unchanged, since `totalKernelStaked` was 0); `updatedAt` is advanced to day 7. Bob's `userRewardPerTokenPaid` is set to the current (frozen) `rewardPerTokenStored`.
5. Bob earns only rewards from day 7 onward. The `6_000e18` reward tokens that accrued during days 2–7 remain in the contract balance permanently, with no mechanism to recover them. [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L18-23)
```text
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
