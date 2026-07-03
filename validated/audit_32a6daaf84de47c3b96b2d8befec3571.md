Audit Report

## Title
Precision loss in `rewardPerToken()` combined with unconditional `updatedAt` advancement causes permanent freezing of unclaimed yield - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`rewardPerToken()` performs integer division that silently discards the remainder `(rewardRate * dt * DECIMAL_PRECISION) % totalKernelStaked`. The `updateReward` modifier unconditionally advances `updatedAt` to the current time even when `rewardPerTokenStored` is unchanged, permanently destroying the reward accrual for that interval. Any external caller can permissionlessly trigger this via `getReward()`, and when `rewardRate * dt * DECIMAL_PRECISION < totalKernelStaked`, the entire reward for interval `dt` is irrecoverably lost.

## Finding Description
`rewardPerToken()` at L412–413 computes:

```solidity
return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / totalKernelStaked;
```

The integer division discards `(rewardRate * dt * DECIMAL_PRECISION) % totalKernelStaked`. The `updateReward` modifier at L232–242 then unconditionally writes back `rewardPerTokenStored = rewardPerToken()` and advances `updatedAt = lastTimeRewardApplicable()`. When the condition `rewardRate * dt * DECIMAL_PRECISION < totalKernelStaked` holds, `rewardPerTokenStored` is unchanged while `updatedAt` is bumped forward by `dt` seconds — the reward accrual for that interval is permanently unaccounted for and the tokens remain locked in the contract.

The permissionless entry point is `getReward()` at L382–390: any external address (including one with zero stake) can call it, triggering `updateReward(msg.sender)`, consuming a time slice without advancing `rewardPerTokenStored`. The exploit path is:

1. Admin calls `notifyRewardAmount`, setting `rewardRate` and `finishAt`.
2. Attacker calls `getReward()` once every `dt` seconds where `dt` satisfies `rewardRate * dt * 1e18 < totalKernelStaked`.
3. Each call: `rewardPerToken()` returns `rewardPerTokenStored + 0`; `updatedAt` advances by `dt`; rewards for `dt` are permanently lost.
4. Repeated for the full `duration`, 100% of distributed rewards remain stuck in the contract.

Existing guards are insufficient: `nonReentrant` prevents reentrancy but not repeated external calls; there is no minimum-interval check on `updateReward`; there is no residual carry-forward mechanism.

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.** Two concrete sub-impacts:

1. **Continuous precision loss**: On every `updateReward` invocation the residual `(rewardRate * dt * 1e18) % totalKernelStaked` is silently discarded and irrecoverable. This occurs on every user interaction regardless of attacker presence.
2. **Full griefing**: When `rewardRate * dt * 1e18 < totalKernelStaked`, the entire reward for interval `dt` is lost. An attacker calling `getReward()` once per `dt` seconds for the full `duration` causes 100% of distributed rewards to remain permanently stuck in the contract, with all stakers earning zero. This matches the allowed impact "Medium. Permanent freezing of unclaimed yield."

## Likelihood Explanation
**Medium.** The continuous precision loss is always present on every `updateReward` call — no attacker required. The full griefing attack requires `totalKernelStaked > rewardRate * 1e18`, which is realistic at any meaningful staking scale: with `totalKernelStaked = 21e18` (21 KERNEL) and `rewardRate = 10` raw tokens/sec, `dt = 2` seconds satisfies the condition. Ethereum's ~12-second block time makes per-block griefing feasible. Higher staking amounts or lower reward rates make the condition easier to satisfy. The attacker needs no stake, no special role, and spends only gas.

## Recommendation
Carry the residual forward into the next computation rather than discarding it:

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
Foundry test demonstrating 100% reward loss via the griefing path:

```solidity
function test_rewardPerTokenStored_KernelDepositPool() public {
    address user1 = address(0xa11ce);
    uint256 rewardDuration = 1 hours;
    uint256 stakingAmount = 21 ether;       // totalKernelStaked = 21e18
    uint256 rewardRate    = 10;             // raw tokens/sec
    uint256 rewardAmount  = rewardRate * rewardDuration;

    vm.startPrank(user1);
    kernelToken.mint(user1, stakingAmount);
    kernelToken.approve(address(pool), stakingAmount);
    pool.stake(stakingAmount);
    vm.stopPrank();

    rewardsToken.mint(address(this), rewardAmount);
    rewardsToken.approve(address(pool), rewardAmount);
    pool.notifyRewardAmount(rewardAmount);

    // dt=2: rewardRate * dt * 1e18 = 20e18 < 21e18 = totalKernelStaked
    uint256 dt = stakingAmount / (rewardRate * 1e18); // = 2 seconds

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