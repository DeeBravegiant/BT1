Audit Report

## Title
Precision loss in `rewardPerToken()` permanently freezes unclaimed yield - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`rewardPerToken()` performs integer division that silently discards the remainder `rewardRate * dt * DECIMAL_PRECISION % totalKernelStaked`. The `updateReward` modifier unconditionally advances `updatedAt` to the current time even when `rewardPerTokenStored` is unchanged, permanently destroying the discarded rewards. When `rewardRate * dt * 1e18 < totalKernelStaked`, the entire reward for interval `dt` is lost. A permissionless attacker calling `getReward()` once per block for the full reward duration can cause 100% of distributed rewards to remain permanently stuck in the contract.

## Finding Description
`rewardPerToken()` at L408–414 computes:

```solidity
rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / totalKernelStaked;
```

The integer division truncates the residual. The `updateReward` modifier at L232–242 then writes the (potentially unchanged) result back to `rewardPerTokenStored` and unconditionally sets `updatedAt = lastTimeRewardApplicable()`. When `rewardRate * dt * 1e18 < totalKernelStaked`, `rewardPerTokenStored` does not increase while `updatedAt` advances by `dt` seconds. The rewards that accrued during `dt` are permanently unaccounted for — they remain in the contract balance but can never be claimed by any user.

The permissionless entry point is `getReward()` at L382–390, which applies `updateReward(msg.sender)` with no access restriction. `stake()` at L281–289 is equally permissionless and has the same effect. An attacker who calls `getReward()` every `dt` seconds for the full `duration` consumes all time slices without ever advancing `rewardPerTokenStored`, causing all stakers to earn zero.

Even without active griefing, the continuous precision loss occurs on every `updateReward` invocation throughout the reward period, accumulating irrecoverable residuals.

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.** Staker yield is permanently locked in the contract and irrecoverable. Two sub-impacts exist: (1) continuous accumulation of discarded residuals on every `updateReward` call, present always; (2) full griefing where 100% of distributed rewards are frozen when the attacker calls `getReward()` at intervals satisfying `rewardRate * dt * 1e18 < totalKernelStaked`. This matches the allowed impact "Medium. Permanent freezing of unclaimed yield."

## Likelihood Explanation
**Medium.** The continuous precision loss is unconditional and always present. The full griefing condition `totalKernelStaked > rewardRate * 1e18` is realistic: with `totalKernelStaked = 21e18` and `rewardRate = 10`, `dt = 2` seconds satisfies the condition, and Ethereum's ~12-second block time makes per-block griefing feasible. Higher staking amounts or lower reward rates make the attack easier. `getReward()` requires no tokens, no stake, and no privilege — any EOA can execute it.

## Recommendation
Introduce a `rewardResidue` state variable to carry the truncated remainder forward into the next computation:

```solidity
uint256 public rewardResidue;

function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) return rewardPerTokenStored;
    uint256 numerator = rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION + rewardResidue;
    return rewardPerTokenStored + numerator / totalKernelStaked;
}

modifier updateReward(address _account) {
    uint256 numerator = rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION + rewardResidue;
    rewardPerTokenStored = rewardPerTokenStored + (totalKernelStaked > 0 ? numerator / totalKernelStaked : 0);
    rewardResidue = totalKernelStaked > 0 ? numerator % totalKernelStaked : rewardResidue;
    updatedAt = lastTimeRewardApplicable();
    if (_account != address(0)) {
        rewards[_account] = earned(_account);
        userRewardPerTokenPaid[_account] = rewardPerTokenStored;
    }
    _;
}
```

## Proof of Concept
The following Foundry test demonstrates 100% reward loss via the griefing path:

```solidity
function test_rewardPerTokenStored_KernelDepositPool() public {
    address user1 = address(0xa11ce);
    uint256 rewardDuration = 1 hours;
    uint256 stakingAmount = 21 ether;   // totalKernelStaked = 21e18
    uint256 rewardRate    = 10;         // raw tokens/sec
    uint256 rewardAmount  = rewardRate * rewardDuration;

    vm.startPrank(user1);
    kernelToken.mint(user1, stakingAmount);
    kernelToken.approve(address(pool), stakingAmount);
    pool.stake(stakingAmount);
    vm.stopPrank();

    rewardsToken.mint(address(this), rewardAmount);
    rewardsToken.approve(address(pool), rewardAmount);
    pool.notifyRewardAmount(rewardAmount);

    // dt = 2s: rewardRate * dt * 1e18 = 20e18 < 21e18 = totalKernelStaked
    uint256 dt = stakingAmount / (rewardRate * 1e18);
    uint256 nSkips = rewardDuration / dt;
    for (uint256 i; i < nSkips; i++) {
        skip(dt);
        pool.getReward(); // permissionless; advances updatedAt, rewardPerTokenStored unchanged
    }

    assertEq(pool.rewardPerTokenStored(), 0);
    assertEq(pool.earned(user1, address(rewardsToken)), 0);
    assertEq(rewardsToken.balanceOf(address(pool)), rewardAmount); // 100% stuck
}
```

Exploit path: any external caller → `getReward()` → `updateReward(msg.sender)` → `rewardPerToken()` integer division truncates to zero → `rewardPerTokenStored` unchanged, `updatedAt` advanced → rewards for `dt` permanently lost. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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
