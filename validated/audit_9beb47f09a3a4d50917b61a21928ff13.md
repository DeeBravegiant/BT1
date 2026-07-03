### Title
Rewards Permanently Locked When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: `contracts/KERNEL/KernelDepositPool.sol`)

### Summary
In `KernelDepositPool.sol`, when all stakers call `initiateWithdrawal` and `totalKernelStaked` reaches zero during an active reward distribution window, the rewards that should have been distributed during the zero-staked interval are permanently locked in the contract. The contract's own NatSpec acknowledges this but relies solely on an off-chain operational mitigation that is not enforced by the code.

### Finding Description
The `rewardPerToken()` function freezes at `rewardPerTokenStored` whenever `totalKernelStaked == 0`:

```solidity
// KernelDepositPool.sol L408-L413
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

The `updateReward` modifier, which is applied to `stake`, `stakeFor`, `initiateWithdrawal`, and `getReward`, always advances `updatedAt` to `lastTimeRewardApplicable()` regardless of whether `totalKernelStaked` is zero:

```solidity
// KernelDepositPool.sol L232-L241
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    updatedAt = lastTimeRewardApplicable();
    ...
}
```

The sequence that causes permanent reward loss:

1. The last staker calls `initiateWithdrawal`. `updateReward` runs: `rewardPerTokenStored` is checkpointed correctly, `updatedAt` is set to time T1, then `totalKernelStaked` drops to 0.
2. Time passes from T1 to T2. `rewardRate` is still active (`finishAt` is in the future), but since `totalKernelStaked == 0`, `rewardPerToken()` returns the frozen `rewardPerTokenStored`. No rewards accumulate.
3. A new staker calls `stake`. `updateReward` runs: `rewardPerTokenStored` stays frozen (still returns `rewardPerTokenStored` because `totalKernelStaked` is still 0 at this point), but **`updatedAt` is advanced to T2**. Then `totalKernelStaked` increases.
4. All rewards that should have been distributed between T1 and T2 are permanently skipped. The `rewardRate * (T2 - T1)` tokens remain locked in the contract forever.

The contract's own NatSpec comment at line 18–22 explicitly acknowledges this:

> *"If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards for that period will stay locked in the contract. In this deployment, we're avoiding this issue by ensuring there are always some tokens staked..."*

This mitigation is purely operational and is not enforced by the contract code. Any user can call `initiateWithdrawal` to reduce `totalKernelStaked`.

### Impact Explanation
**Medium — Permanent freezing of unclaimed yield.** Reward tokens (`rewardsToken`) are permanently locked in the `KernelDepositPool` contract with no recovery mechanism. The amount lost equals `rewardRate × (duration of zero-staked period)`. For a 30-day reward window with a meaningful `rewardRate`, this can represent a significant portion of the total reward allocation.

### Likelihood Explanation
**Medium.** The scenario requires `totalKernelStaked` to reach zero during an active reward period. This can happen organically if stakers lose confidence and all withdraw, or if the withdrawal delay is short enough that a coordinated exit is feasible. The `MAX_WITHDRAWAL_DELAY` is 30 days, but the actual `withdrawalDelay` is set by the admin and could be much shorter. No privileged access is required to trigger this — any combination of stakers calling `initiateWithdrawal` is sufficient.

### Recommendation
Advance `updatedAt` only when `totalKernelStaked > 0`, so that the zero-staked interval's rewards are not silently skipped when the next staker arrives. Alternatively, when `totalKernelStaked` drops to zero, snapshot the remaining undistributed rewards and allow them to be rolled into the next reward period via `notifyRewardAmount`.

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    // Only advance updatedAt when there are stakers to receive rewards
    if (totalKernelStaked > 0) {
        updatedAt = lastTimeRewardApplicable();
    }
    ...
}
```

### Proof of Concept

1. Admin calls `notifyRewardAmount(1_000e18)` with `totalKernelStaked = 100e18`. `rewardRate = 1_000e18 / duration`. `finishAt = block.timestamp + duration`.
2. Alice (the only staker) calls `initiateWithdrawal(100e18)`. `updateReward` checkpoints `rewardPerTokenStored` and sets `updatedAt = T1`. `totalKernelStaked` → 0.
3. 15 days pass (half the reward period). `rewardRate × 15 days` worth of rewards accumulate in the contract but are not distributed to anyone.
4. Bob calls `stake(1e18)`. `updateReward` runs: `rewardPerToken()` returns frozen `rewardPerTokenStored` (since `totalKernelStaked` is still 0 before the stake). `updatedAt` is advanced to T2 (15 days after T1). `totalKernelStaked` → 1e18.
5. Bob's `userRewardPerTokenPaid` = `rewardPerTokenStored`. Bob only earns rewards from T2 onward.
6. The `rewardRate × 15 days` tokens from step 3 are permanently locked in the contract with no way to recover them. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L566-591)
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
```
