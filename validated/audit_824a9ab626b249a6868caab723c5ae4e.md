### Title
Reward Tokens Permanently Locked When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

---

### Summary

`KernelDepositPool` contains a structural analog to the reference division-by-zero / reward-freeze vulnerability. When `totalKernelStaked` drops to zero during an active reward distribution window — which any staker can trigger by calling `initiateWithdrawal` — the `updateReward` modifier advances `updatedAt` past the zero-supply interval without distributing any rewards. Those reward tokens are permanently locked in the contract with no recovery path.

---

### Finding Description

The `rewardPerToken()` function correctly guards against division by zero:

```solidity
// KernelDepositPool.sol L408-413
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;   // no accumulation
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

However, the `updateReward` modifier unconditionally advances `updatedAt` to `lastTimeRewardApplicable()` on every call, regardless of whether `totalKernelStaked` is zero:

```solidity
// KernelDepositPool.sol L232-242
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    updatedAt = lastTimeRewardApplicable();   // always advances
    ...
}
```

When `totalKernelStaked == 0`, `rewardPerToken()` returns the frozen `rewardPerTokenStored`. Then `updatedAt` is set to the current time. The next time `rewardPerToken()` is called with `totalKernelStaked > 0`, the formula computes `rewardRate * (lastTimeRewardApplicable() - updatedAt)` — but `updatedAt` was already advanced past the zero-supply gap, so the rewards that accrued during that gap are silently skipped and permanently unclaimable.

`initiateWithdrawal` (L320-338) decrements `totalKernelStaked` with no floor check:

```solidity
// KernelDepositPool.sol L325-326
balanceOf[msg.sender] -= _amount;
totalKernelStaked -= _amount;
```

`notifyRewardAmount` (L566-592) only checks `totalKernelStaked == 0` at reward-period start, not during the period:

```solidity
// KernelDepositPool.sol L569-570
// Prevent starting a reward period when no tokens are staked to avoid unallocated rewards
if (totalKernelStaked == 0) revert NoStakedTokens();
```

The contract's own NatSpec (L18-22) acknowledges the issue but relies entirely on an off-chain deployment convention ("ensuring there are always some tokens staked"), which is not enforced by any code invariant.

There is no `recoverERC20` or equivalent admin function in the contract, so locked reward tokens cannot be retrieved.

---

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

Reward tokens deposited by the admin via `notifyRewardAmount` are permanently locked in the contract for any time interval during which `totalKernelStaked == 0`. No user can claim them (no one was staked), and no admin function can recover them. The protocol fails to deliver the promised reward distribution for that interval.

---

### Likelihood Explanation

Any staker can call `initiateWithdrawal` at any time for their full balance. If they are the sole staker, or if all stakers withdraw simultaneously (e.g., in response to a market event), `totalKernelStaked` drops to zero mid-period. This is a normal, permissionless user action with no special preconditions beyond being the last staker. The `withdrawalDelay` does not prevent the accounting gap — `totalKernelStaked` is decremented immediately at `initiateWithdrawal` time (L326), not at `claimWithdrawal` time.

---

### Recommendation

In `rewardPerToken()`, skip advancing `updatedAt` when `totalKernelStaked == 0`, so the elapsed zero-supply time is not consumed:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;
    }
    return rewardPerTokenStored
        + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

And in the `updateReward` modifier, only advance `updatedAt` when `totalKernelStaked > 0`:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    if (totalKernelStaked > 0) {
        updatedAt = lastTimeRewardApplicable();
    }
    ...
}
```

This mirrors the fix applied in the reference report: skip the problematic accounting step when the divisor/supply is zero, preserving the unallocated rewards for future stakers.

---

### Proof of Concept

**Setup:** Single staker Alice, active reward period.

1. Admin calls `notifyRewardAmount(1000e18)` with `totalKernelStaked = 100e18` → `rewardRate = 1000e18 / duration`, `finishAt = T + duration`, `updatedAt = T`.
2. At time `T+100`: Alice calls `initiateWithdrawal(100e18)`.
   - `updateReward(Alice)` fires: `rewardPerTokenStored` accumulates Alice's share for `[T, T+100]`. `updatedAt = T+100`. `totalKernelStaked = 0`.
3. At time `T+200`: Bob calls `stake(50e18)`.
   - `updateReward(Bob)` fires: `rewardPerToken()` returns `rewardPerTokenStored` (guard at L409). `updatedAt = T+200`. `totalKernelStaked = 50e18`.
4. At time `T+300`: Bob calls `getReward()`.
   - `earned(Bob)` = `50e18 * (rewardPerToken() - userRewardPerTokenPaid[Bob]) / 1e18`.
   - `rewardPerToken()` now accumulates only from `T+200` to `T+300` (100 seconds), not from `T+100` to `T+200`.
   - The `rewardRate * 100` tokens for the `[T+100, T+200]` zero-supply window are permanently locked in the contract. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
