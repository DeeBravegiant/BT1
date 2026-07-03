Audit Report

## Title
Rewards Permanently Frozen When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
In `KernelDepositPool`, the `updateReward` modifier unconditionally advances `updatedAt` to `lastTimeRewardApplicable()` even when `totalKernelStaked == 0`, while `rewardPerToken()` silently skips accrual during that same zero-stake window. Any `rewardRate × gap_duration` tokens emitted during the gap are permanently unaccounted for and locked in the contract with no recovery path.

## Finding Description
`rewardPerToken()` returns `rewardPerTokenStored` unchanged when `totalKernelStaked == 0`: [1](#0-0) 

The `updateReward` modifier always writes `updatedAt = lastTimeRewardApplicable()` regardless of supply: [2](#0-1) 

`initiateWithdrawal` reduces `totalKernelStaked` immediately at initiation, not at claim time: [3](#0-2) 

This creates the gap scenario: when Alice (sole staker) calls `initiateWithdrawal(all)`, `totalKernelStaked` drops to 0 immediately. During the mandatory `withdrawalDelay` (up to `MAX_WITHDRAWAL_DELAY = 30 days`), `rewardRate` continues emitting tokens but `rewardPerToken()` returns the stale stored value. When Bob later stakes, `updateReward` fires — `rewardPerToken()` still returns the old stored value (gap skipped), but `updatedAt` jumps forward to the current timestamp. Bob only earns rewards from his stake time onward; the gap rewards are gone. [4](#0-3) 

The `notifyRewardAmount` guard only prevents *starting* a new reward period with zero stakers; it does not prevent all stakers from exiting during an already-active period: [5](#0-4) 

The contract's own NatSpec acknowledges this limitation but relies entirely on an off-chain operational assumption with no code-level enforcement: [6](#0-5) 

There is no `rescueTokens`, no `sweep`, and no admin function to recover stuck reward tokens.

## Impact Explanation
Reward tokens transferred into the contract via `notifyRewardAmount` and emitted during any zero-stake gap are permanently frozen in the contract. The lost amount scales with `rewardRate × gap_duration`. With a 30-day withdrawal delay, this can represent a substantial portion of a reward period's total emissions. This maps to **Medium — Permanent freezing of unclaimed yield**.

## Likelihood Explanation
The scenario is reachable by any single unprivileged staker who is the sole depositor — realistic early in the protocol lifecycle or after a mass exit event. No privileged access is required: `initiateWithdrawal` is a public user function. The `withdrawalDelay` up to 30 days makes the gap window large and the lost amount proportionally significant. No code-level guard prevents `totalKernelStaked` from reaching zero during an active reward period. Likelihood is **Medium**.

## Recommendation
Do not advance `updatedAt` when `totalKernelStaked == 0`. In the `updateReward` modifier, gate the timestamp advancement on supply being nonzero:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    if (totalKernelStaked > 0) {
        updatedAt = lastTimeRewardApplicable();
    }
    if (_account != address(0)) {
        rewards[_account] = earned(_account);
        userRewardPerTokenPaid[_account] = rewardPerTokenStored;
    }
    _;
}
```

This preserves the gap window in `updatedAt` so that when the next staker enters, `rewardPerToken()` replays the full gap and distributes those rewards correctly.

## Proof of Concept

```solidity
// Setup: Alice is sole staker, admin starts reward period
kernelDepositPool.setRewardsDuration(30 days);
kernelDepositPool.notifyRewardAmount(30_000e18); // rewardRate ≈ 1000e18/day

// Alice initiates full withdrawal → totalKernelStaked = 0
vm.prank(alice);
kernelDepositPool.initiateWithdrawal(aliceStake);

// 10 days pass with totalKernelStaked == 0
// 10_000e18 reward tokens emitted but credited to no one
vm.warp(block.timestamp + 10 days);

// Bob stakes — updateReward fires, updatedAt jumps to now, gap rewards lost
vm.prank(bob);
kernelDepositPool.stake(1e18);

// 10 more days pass
vm.warp(block.timestamp + 10 days);

// Bob claims — receives only ~10_000e18 (T=20→30), not ~20_000e18
vm.prank(bob);
kernelDepositPool.getReward();

// Assert: ~10_000e18 permanently stuck in contract
assertGt(rewardsToken.balanceOf(address(kernelDepositPool)), 9_000e18);
```

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L35-35)
```text
    uint256 public constant MAX_WITHDRAWAL_DELAY = 30 days;
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
