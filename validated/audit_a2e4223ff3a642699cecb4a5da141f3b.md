Audit Report

## Title
Rewards emitted during zero-staked intervals are permanently frozen — (`contracts/KERNEL/KernelDepositPool.sol`)

## Summary

In `KernelDepositPool`, the `updateReward` modifier unconditionally advances `updatedAt` to `lastTimeRewardApplicable()` even when `totalKernelStaked == 0`. Because `rewardPerToken()` short-circuits and returns the unchanged `rewardPerTokenStored` in that case, any rewards emitted at `rewardRate` during a zero-staked interval are silently skipped and permanently locked in the contract. The contract's own NatSpec acknowledges this behavior but relies solely on an off-chain operational promise rather than an on-chain invariant.

## Finding Description

The `updateReward` modifier executes two steps unconditionally:

```solidity
// L232-242
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();   // (A) no-op when totalKernelStaked == 0
    updatedAt = lastTimeRewardApplicable();    // (B) always advances the clock
    ...
}
```

`rewardPerToken()` short-circuits at L409-410 when `totalKernelStaked == 0`, returning `rewardPerTokenStored` unchanged. Step (A) therefore leaves the global index frozen, but step (B) still consumes the elapsed time by advancing `updatedAt`. The `rewardRate * elapsed` rewards that should have accrued over that interval are permanently unaccounted for.

`initiateWithdrawal` decrements `totalKernelStaked` immediately at L325-326 before returning, so a full withdrawal by all stakers sets `totalKernelStaked = 0` mid-period. When the next staker calls `stake()`, `updateReward` fires *before* `totalKernelStaked` is incremented (L281-285), so `totalKernelStaked` is still 0 at that moment, `rewardPerToken()` again returns the stale stored value, and `updatedAt` is pushed forward once more. The entire zero-staked gap is consumed with no reward distribution.

The `notifyRewardAmount` guard at L570 (`if (totalKernelStaked == 0) revert NoStakedTokens()`) only prevents *starting* a new period with zero stakers; it provides no protection against stakers withdrawing *during* an active period.

The contract's own NatSpec at L18-22 explicitly acknowledges the issue but defers to an off-chain operational promise ("ensuring there are always some tokens staked"), which is not enforced by any on-chain invariant.

## Impact Explanation

Any rewards emitted at `rewardRate` during the interval `[t_zero_staked, t_next_stake]` are permanently frozen in the contract — no user can ever claim them. This matches **Medium — Permanent freezing of unclaimed yield**.

## Likelihood Explanation

The scenario requires all current stakers to call `initiateWithdrawal` before any new staker arrives. No privileged key or admin action is needed; ordinary stakers trigger it through the public `initiateWithdrawal` entry point. This is realistic during low-activity periods, market downturns, or when the staker set is small. The condition is repeatable across multiple reward periods.

## Recommendation

In the `updateReward` modifier, only advance `updatedAt` when there are stakers to receive rewards:

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

This ensures the accounting clock only advances when rewards are actually being distributed, mirroring the standard Synthetix fix for this class of bug. Alternatively, when `notifyRewardAmount` is called after a zero-staked gap, compute the unallocated rewards and roll them into the new `rewardRate` so no tokens are stranded.

## Proof of Concept

```
T=0    notifyRewardAmount(1000 KERNEL, duration=1000s)
       → rewardRate=1/s, finishAt=T+1000, updatedAt=0
       totalKernelStaked=100 (Alice staked 100)

T=10   Alice calls initiateWithdrawal(100)
       → updateReward: rewardPerTokenStored += 1*10*1e18/100 = 0.1e18
         updatedAt = 10
       → totalKernelStaked = 0

       [T=10 to T=500: no stakers, 490 KERNEL emitted, nobody receives them]

T=500  Bob calls stake(100)
       → updateReward fires BEFORE totalKernelStaked incremented:
         rewardPerToken() → totalKernelStaked==0 → returns 0.1e18 (unchanged)
         updatedAt = 500
       → totalKernelStaked = 100, userRewardPerTokenPaid[Bob] = 0.1e18

T=1000 finishAt reached. Bob calls getReward():
       earned(Bob) = 100 * (rewardPerToken() - 0.1e18) / 1e18
                   = 100 * (0.1e18 + 1*(1000-500)*1e18/100 - 0.1e18) / 1e18
                   = 500 KERNEL

       Alice earned: 10 KERNEL (T=0 to T=10)
       Total claimable: 510 KERNEL
       Permanently frozen: 490 KERNEL (T=10 to T=500 interval)
```

A Foundry test can reproduce this by: (1) deploying the contract, (2) staking as Alice, (3) calling `notifyRewardAmount`, (4) having Alice call `initiateWithdrawal` at T=10, (5) warping to T=500, (6) having Bob stake, (7) warping to T=1000, (8) asserting that `rewardsToken.balanceOf(contract) > 0` after both Alice and Bob claim all rewards.