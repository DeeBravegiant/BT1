The code at line 570 shows `notifyRewardAmount` **does** have the `require(totalKernelStaked > 0)` guard the submitter claims is missing. However, the core vulnerability — `totalKernelStaked` dropping to zero **after** a reward period starts — is real and unguarded. The `rewardPerToken()` short-circuit at line 409 still applies mid-period, and there is no mechanism to prevent all stakers from exiting via `initiateWithdrawal` after `notifyRewardAmount` is called.

---

Audit Report

## Title
Rewards Permanently Locked When All Stakers Exit During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`KernelDepositPool` guards `notifyRewardAmount` against a zero-staker start, but provides no protection against all stakers exiting via `initiateWithdrawal` after a reward period begins. When `totalKernelStaked` reaches zero mid-period, `rewardPerToken()` stops advancing and all rewards accruing during the zero-staking interval are permanently locked with no recovery path.

## Finding Description
`notifyRewardAmount` (L570) reverts if `totalKernelStaked == 0` at call time, which prevents starting a reward period with no stakers. However, `initiateWithdrawal` (L320–338) decrements `totalKernelStaked` with no floor check and is callable by any staker at any time during an active reward window. Once `totalKernelStaked == 0`, `rewardPerToken()` (L408–414) short-circuits:

```solidity
if (totalKernelStaked == 0) {
    return rewardPerTokenStored;
}
```

`rewardRate` continues to tick but no accumulator advances. No user's `rewards[user]` mapping is credited for the zero-staking interval. There is no admin sweep, rescue, or re-injection function. The reward tokens transferred in by `notifyRewardAmount` (L573–577) remain permanently stranded in the contract for the duration of the zero-staking gap.

## Impact Explanation
Reward tokens already transferred into the contract become permanently unrecoverable for any interval where `totalKernelStaked == 0`. This is **permanent freezing of unclaimed yield** — a valid Medium impact under the allowed scope.

## Likelihood Explanation
Any staker can call `initiateWithdrawal` for their full balance at any time after a reward period starts. A single sole depositor exiting empties the pool. The withdrawal delay only delays the token transfer; `totalKernelStaked` is decremented immediately at `initiateWithdrawal` time (L325–326), not at `claimWithdrawal` time. No minimum staked floor, no lock-in during active reward periods, and no on-chain invariant prevents this state.

## Recommendation
1. In `initiateWithdrawal`, after decrementing `totalKernelStaked`, check whether a reward period is active (`block.timestamp < finishAt`) and revert or pause if the result would be zero stakers.
2. Alternatively, track cumulative zero-staking time and extend `finishAt` by that duration so no rewards are lost.
3. Add an admin rescue function callable only after `finishAt` to recover rewards that were not distributed due to zero-staking gaps.

## Proof of Concept
1. Admin calls `notifyRewardAmount(1000e18)` with `duration = 7 days`; `rewardRate = 1000e18 / 7 days`. Passes because Alice has `100e18` staked.
2. After 3 days, Alice calls `initiateWithdrawal(100e18)`. `totalKernelStaked` drops to `0` immediately (L325–326). Alice waits out `withdrawalDelay` and calls `claimWithdrawal`.
3. For the remaining 4 days, every call to `rewardPerToken()` returns `rewardPerTokenStored` unchanged (L409–410).
4. `≈ 571e18` reward tokens (`rewardRate * 4 days`) accumulate as unallocated contract balance. No function exists to recover them.
5. Foundry invariant test: after any sequence of `stake`/`initiateWithdrawal`/`claimWithdrawal` calls that drives `totalKernelStaked` to zero mid-period, assert `rewardsToken.balanceOf(address(pool)) == sum(rewards[user] for all users)` — this invariant will be violated by the stranded amount.