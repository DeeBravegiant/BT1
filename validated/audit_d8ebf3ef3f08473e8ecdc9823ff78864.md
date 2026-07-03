### Title
`KernelDepositPool::rewardPerToken()` permanently freezes rewards when `totalKernelStaked` drops to zero mid-period - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.rewardPerToken()` skips accumulating rewards into `rewardPerTokenStored` whenever `totalKernelStaked == 0`, and the `updateReward` modifier simultaneously advances `updatedAt` to the current timestamp. Any rewards that accrued during the zero-staked window are permanently untrackable and locked in the contract.

### Finding Description
`rewardPerToken()` implements the standard Synthetix guard:

```solidity
// contracts/KERNEL/KernelDepositPool.sol:408-414
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;   // accumulation skipped
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

The `updateReward` modifier calls both `rewardPerToken()` and advances `updatedAt`:

```solidity
// contracts/KERNEL/KernelDepositPool.sol:232-242
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();   // returns stale value when supply=0
    updatedAt = lastTimeRewardApplicable();    // timestamp advances regardless
    ...
}
```

`initiateWithdrawal` reduces `totalKernelStaked` immediately — before the `withdrawalDelay` elapses — while the reward period (`finishAt`) remains active:

```solidity
// contracts/KERNEL/KernelDepositPool.sol:320-337
function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
    ...
    balanceOf[msg.sender] -= _amount;
    totalKernelStaked -= _amount;   // immediately zero if last staker
    ...
}
```

When the last staker calls `initiateWithdrawal`, `totalKernelStaked` drops to 0 while `rewardRate` and `finishAt` remain unchanged. Every subsequent call to `updateReward` (e.g., when a new staker arrives) will:
1. Call `rewardPerToken()` → returns `rewardPerTokenStored` unchanged (zero-staked branch).
2. Advance `updatedAt` to `lastTimeRewardApplicable()`.

The time gap during which no one was staked is silently consumed. The rewards that should have been distributed during that window are permanently irrecoverable — there is no admin sweep function.

The contract's own NatSpec acknowledges this at lines 18–22:
> *"If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards for that period will stay locked in the contract."*

The guard in `notifyRewardAmount` (`if (totalKernelStaked == 0) revert NoStakedTokens()`) only prevents *starting* a new period with zero stakers; it does not prevent the mid-period drain.

### Impact Explanation
Rewards (the `rewardsToken`) that accrued during the zero-staked window are permanently locked in the `KernelDepositPool` contract with no recovery path. This constitutes **permanent freezing of unclaimed yield**.

### Likelihood Explanation
Any staker can be the last to call `initiateWithdrawal`, reducing `totalKernelStaked` to 0 during an active reward period. The `withdrawalDelay` (up to 30 days) creates a guaranteed window during which rewards are lost. No coordination or privileged access is required; a single ordinary user action is sufficient.

### Recommendation
When `totalKernelStaked` drops to zero, pause the reward clock rather than silently advancing `updatedAt`. One approach: only advance `updatedAt` when `totalKernelStaked > 0`:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    if (totalKernelStaked > 0) {
        updatedAt = lastTimeRewardApplicable();
    }
    ...
}
```

This ensures that when staking resumes, the rewards for the zero-staked period are distributed to the returning stakers rather than being silently discarded.

### Proof of Concept
1. Admin calls `notifyRewardAmount(1000e18)` with `totalKernelStaked > 0`. `rewardRate = 1000e18 / duration`, `finishAt = block.timestamp + duration`.
2. The sole staker calls `initiateWithdrawal(totalKernelStaked)`. `totalKernelStaked → 0`. `updateReward` fires: `rewardPerTokenStored` is correctly updated for the staker's earned amount, `updatedAt = block.timestamp`.
3. `withdrawalDelay` seconds pass (e.g., 7 days). During this entire window `rewardRate` is still active but `rewardPerToken()` returns `rewardPerTokenStored` unchanged on every call.
4. A new staker calls `stake(1)`. `updateReward` fires: `rewardPerToken()` returns `rewardPerTokenStored` (unchanged, zero-staked branch), but `updatedAt` advances to `block.timestamp`. The 7-day worth of rewards (`rewardRate * 7 days`) is permanently lost.
5. The original staker calls `claimWithdrawal`. Tokens returned, but the 7-day rewards remain locked in the contract forever. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-337)
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L566-570)
```text
    function notifyRewardAmount(uint256 _amount) external onlyRole(DEFAULT_ADMIN_ROLE) updateReward(address(0)) {
        if (_amount == 0) revert AmountZero();

        // Prevent starting a reward period when no tokens are staked to avoid unallocated rewards
        if (totalKernelStaked == 0) revert NoStakedTokens();
```
