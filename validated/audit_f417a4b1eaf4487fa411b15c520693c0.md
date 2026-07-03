### Title
Reward tokens permanently locked when `totalKernelStaked` drops to zero during an active reward window - (File: `contracts/KERNEL/KernelDepositPool.sol`)

### Summary
In `KernelDepositPool.sol`, when all stakers call `initiateWithdrawal()` during an active reward distribution window, `totalKernelStaked` immediately drops to zero. The `rewardPerToken()` function returns `rewardPerTokenStored` unchanged for the entire zero-staked interval, but the `updateReward` modifier still advances `updatedAt` past that interval. The reward tokens that accrued during the zero-staked gap are permanently locked in the contract with no recovery path.

### Finding Description

The contract is a Synthetix-style staking rewards contract. Rewards are distributed continuously at `rewardRate` tokens per second over a `duration`-second window set by `notifyRewardAmount()`.

The `rewardPerToken()` view function handles the zero-supply case by returning the stored value unchanged:

```solidity
// KernelDepositPool.sol L408-413
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;   // ← accumulation frozen
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

The `updateReward` modifier, however, always advances `updatedAt` regardless of whether `totalKernelStaked` is zero:

```solidity
// KernelDepositPool.sol L232-242
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    updatedAt = lastTimeRewardApplicable();   // ← time always advances
    ...
}
```

`initiateWithdrawal()` immediately decrements `totalKernelStaked` before any time-lock:

```solidity
// KernelDepositPool.sol L325-326
balanceOf[msg.sender] -= _amount;
totalKernelStaked -= _amount;
```

**Sequence of events that locks rewards:**

1. Admin calls `notifyRewardAmount(R)` while stakers are present → `rewardRate = R/duration`, `finishAt = now + duration`, `updatedAt = now`.
2. All stakers call `initiateWithdrawal()` → `totalKernelStaked = 0`. The `updateReward` modifier runs, advancing `updatedAt` to the current time and freezing `rewardPerTokenStored`.
3. For the entire zero-staked interval `[t_withdraw, t_restake]`, `rewardPerToken()` returns the frozen `rewardPerTokenStored` while `updatedAt` keeps advancing on every interaction.
4. When a new staker calls `stake()`, `updateReward` runs again: `rewardPerTokenStored` is still the frozen value, but `updatedAt` is now `t_restake`. The `rewardRate * (t_restake - t_withdraw)` tokens that accrued during the gap are **never credited to anyone**.
5. Those tokens remain as excess balance in the contract. There is no `rescueTokens` or equivalent function.

The `notifyRewardAmount` rollover path (`remaining = (finishAt - block.timestamp) * rewardRate`) does **not** recover the gap tokens — it only accounts for future time-based distribution, not the already-skipped interval.

The contract's own NatSpec acknowledges the issue but relies entirely on an off-chain operational assumption with no on-chain enforcement:

```
// KernelDepositPool.sol L18-22
* @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
*      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
*      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
*      as well as for the entire duration of the reward period.
```

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

Any reward tokens that accrued during a zero-staked interval are irrecoverably locked in the contract. There is no admin rescue function, no rollover mechanism for the gap, and no way for stakers to claim those tokens. The amount lost equals `rewardRate × (duration of zero-staked interval)`.

### Likelihood Explanation

**Low/Medium.** The scenario requires all stakers to withdraw during an active reward window. This is realistic because:
- `initiateWithdrawal()` is permissionless for any staker.
- The withdrawal delay only delays token retrieval, not the immediate decrement of `totalKernelStaked`.
- A coordinated or organic exit (e.g., market downturn, better yield elsewhere) can drain all stakers within a single reward period.
- The protocol has no on-chain mechanism to prevent this; the mitigation is purely operational.

### Recommendation

Add an on-chain guard in `initiateWithdrawal()` that prevents `totalKernelStaked` from reaching zero during an active reward window, or track the zero-staked interval and carry the unallocated rewards forward into the next period. For example:

```solidity
// Option A: block full withdrawal during active reward window
if (block.timestamp < finishAt && totalKernelStaked - _amount == 0) {
    revert CannotDrainStakeDuringRewardPeriod();
}

// Option B: in rewardPerToken(), carry forward unallocated rewards
// by tracking a separate `lostRewards` accumulator when totalKernelStaked == 0
// and rolling it into the next notifyRewardAmount call.
```

Alternatively, add an admin `rescueRewardTokens()` function that can recover excess reward balance (actual balance minus owed rewards) to prevent permanent lock.

### Proof of Concept

```solidity
// Setup: duration = 100s, rewardRate = 1e18 tokens/s, 1 staker with 1e18 KERNEL
// T=0: notifyRewardAmount(100e18) → finishAt=100, updatedAt=0
// T=10: staker calls initiateWithdrawal(1e18)
//   → updateReward runs: rewardPerTokenStored += 1e18*10*1e18/1e18 = 10e18
//   → updatedAt = 10
//   → totalKernelStaked = 0
// T=20: new staker calls stake(1e18)
//   → updateReward runs: rewardPerToken() returns rewardPerTokenStored (10e18, unchanged)
//   → updatedAt = 20   ← gap [10,20] is skipped
//   → totalKernelStaked = 1e18
// T=100: finishAt reached
//   → new staker earned: (100-20)*1e18 = 80e18 tokens
//   → contract holds: 100e18 total - 10e18 (original staker) - 80e18 (new staker) = 10e18 LOCKED
//   → the 10e18 tokens from the [T=10, T=20] gap are permanently stuck
assertEq(rewardsToken.balanceOf(address(pool)), 10e18); // stuck forever
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-326)
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
