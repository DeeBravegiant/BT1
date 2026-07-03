Audit Report

## Title
Rewards Permanently Locked When `totalKernelStaked` Drops to Zero Mid-Period - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`KernelDepositPool` implements a Synthetix-style staking rewards model where `rewardPerToken()` silently halts reward accrual when `totalKernelStaked == 0`. Because `initiateWithdrawal()` immediately decrements `totalKernelStaked` while the `updateReward` modifier still advances `updatedAt`, any reward tokens that should have accrued during a zero-stake interval are permanently locked in the contract with no recovery path.

## Finding Description
The `rewardPerToken()` function at [1](#0-0)  returns `rewardPerTokenStored` unchanged when `totalKernelStaked == 0`. Simultaneously, the `updateReward` modifier at [2](#0-1)  always executes `updatedAt = lastTimeRewardApplicable()`, advancing the timestamp checkpoint regardless of whether any rewards were actually accrued. The combination means the elapsed zero-stake time is consumed and the corresponding `rewardRate * elapsed` tokens are never attributed to any account.

The `initiateWithdrawal()` function at [3](#0-2)  decrements `totalKernelStaked` immediately at call time. The withdrawal delay only governs when the underlying KERNEL tokens are returned; it has no effect on the staking accounting. The `notifyRewardAmount` guard at [4](#0-3)  only prevents starting a new period with zero stakers and does not protect against mid-period drops to zero.

The contract's own NatSpec at [5](#0-4)  acknowledges this exact failure mode but relies entirely on an off-chain operational guarantee ("ensuring there are always some tokens staked"), which cannot be enforced on-chain. There is no admin sweep, rescue, or rollover function anywhere in the contract to recover locked reward tokens.

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.** Reward tokens equal to `rewardRate × zero_stake_duration` are irrecoverably locked in the contract. The `rewardsToken` balance of the contract will permanently exceed the sum of all claimable `rewards[user]` values by exactly this amount. No account can ever claim these tokens, and no admin function exists to recover or roll them forward.

## Likelihood Explanation
Any single staker holding 100% of `totalKernelStaked` can trigger this by calling `initiateWithdrawal(totalBalance)` during an active reward period. No privileged access, collusion, or external dependency is required. The action is a normal user workflow (withdrawal initiation) available to any unprivileged address. The scenario is repeatable across every reward period.

## Recommendation
When `totalKernelStaked` returns to a nonzero value after a zero-stake gap, recalculate `rewardRate` to include the rewards that were not distributed during the gap — analogous to how `notifyRewardAmount` handles a mid-period top-up using `remaining` at [6](#0-5) . Concretely, track a `deadTime` accumulator: when `totalKernelStaked` drops to zero, record `deadTimeStart = lastTimeRewardApplicable()`; when it becomes nonzero again (in `stake`/`stakeFor`), add the elapsed gap to a `totalDeadTime` and adjust `rewardRate = (remaining_undistributed_rewards) / (finishAt - block.timestamp)`. This preserves user withdrawal freedom while ensuring no rewards are permanently lost.

## Proof of Concept
1. Admin calls `notifyRewardAmount(1_000_000e18)` with `duration = 30 days`. `rewardRate ≈ 385e18/day`. Alice is the sole staker with `balanceOf[Alice] = 1000e18`.
2. At `t = 10 days`, Alice calls `initiateWithdrawal(1000e18)`. The `updateReward(Alice)` modifier correctly credits Alice ~333,333e18 for 10 days. Then `totalKernelStaked = 0` and `updatedAt = t+10d`.
3. At `t = 20 days`, Bob calls `stake(1000e18)`. `rewardPerToken()` returns the same `rewardPerTokenStored` as step 2 (since `totalKernelStaked` was 0 the entire interval). `updatedAt` was advanced to `t+20d` by the modifier. Bob's `userRewardPerTokenPaid[Bob]` is set to this value.
4. The period ends at `t = 30 days`. Bob earns only ~333,333e18 (10 days of rewards), not ~666,666e18 (20 days).
5. The ~333,333e18 tokens from the 10-day zero-stake window remain in the contract permanently. `rewardsToken.balanceOf(contract)` exceeds the sum of all claimable `rewards[user]` by this amount, with no mechanism to recover it.

**Foundry invariant test sketch:**
```solidity
// Invariant: rewardsToken.balanceOf(pool) >= sum(rewards[u] for all u) + undistributed
// After a zero-stake gap, assert pool balance > sum of claimable rewards
assertGt(rewardsToken.balanceOf(address(pool)), pool.rewards(alice) + pool.rewards(bob));
```

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L18-22)
```text
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L569-570)
```text
        // Prevent starting a reward period when no tokens are staked to avoid unallocated rewards
        if (totalKernelStaked == 0) revert NoStakedTokens();
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L581-583)
```text
        } else {
            uint256 remaining = (finishAt - block.timestamp) * rewardRate;
            rewardRate = (receivedAmount + remaining) / duration;
```
