Audit Report

## Title
Reward Tokens Permanently Locked When `totalKernelStaked` Drops to Zero During Active Reward Window - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`KernelDepositPool` distributes reward tokens over a time-based window. If all stakers exit via `initiateWithdrawal` during an active reward period, `totalKernelStaked` drops to zero and `rewardPerToken()` stops accumulating. The `rewardRate * remainingTime` worth of reward tokens become permanently locked in the contract with no on-chain recovery path. The contract's own NatSpec acknowledges this behavior but relies solely on off-chain operational controls, which are not enforced by the contract itself.

## Finding Description
`rewardPerToken()` (L408–414) short-circuits when `totalKernelStaked == 0`, returning `rewardPerTokenStored` unchanged:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;
    }
    ...
}
```

The `updateReward` modifier (L232–234) snapshots `updatedAt = lastTimeRewardApplicable()` on every user action. When the last staker calls `initiateWithdrawal` (L320–338), `totalKernelStaked` is decremented to zero and `updatedAt` is set to the current timestamp. From that point, `rewardPerToken()` always returns the same stored value, so `rewardRate * (finishAt - updatedAt)` worth of reward tokens accumulate in the contract balance with no path to distribute or recover them.

The guard in `notifyRewardAmount` (L570) — `if (totalKernelStaked == 0) revert NoStakedTokens()` — only prevents starting a new reward period with zero stakers. It does not prevent all stakers from exiting during an already-active period, and it does not unblock the stranded tokens from a prior period.

No admin function in `KernelDepositPool` can recover `rewardsToken`. The entire contract (L1–621) contains no `withdrawTokens`, `recoverERC20`, or equivalent rescue function for the rewards token. The NatSpec at L18–22 explicitly acknowledges this limitation but states the mitigation is purely operational ("ensuring there are always some tokens staked... for the entire duration of the reward period"), which is not enforced on-chain.

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.**

Any reward tokens corresponding to the interval `[lastStakerExit, finishAt]` are permanently locked. If `rewardRate = R` and remaining time is `T` seconds, then `R * T` reward tokens are irrecoverably stranded. No future call to `notifyRewardAmount` can unlock them; it only starts a fresh window on top of the stranded balance. This matches the allowed impact: **Medium. Permanent freezing of unclaimed yield.**

## Likelihood Explanation
**Medium.** The trigger is normal, permissionless user behavior — any staker calling `initiateWithdrawal`. If the last staker exits before `finishAt`, the condition is met. No admin collusion, key compromise, or external protocol failure is required. In a low-TVL or end-of-campaign scenario this is realistic and repeatable.

## Recommendation
1. Add an admin-callable `recoverRewardTokens(uint256 amount)` function (gated to `block.timestamp >= finishAt`) that transfers stranded reward tokens to a designated address, similar to `KernelTop100MerkleDistributor.withdrawTokens` (L461–472).
2. Alternatively, on each `updateReward` call when `totalKernelStaked == 0`, accumulate the skipped rewards into an `undistributedRewards` variable and allow the admin to roll them into the next window via `notifyRewardAmount`.

## Proof of Concept
1. Admin calls `setRewardsDuration(7 days)`, then `notifyRewardAmount(700_000e18)` with Alice staked → `rewardRate = 100_000e18/day`, `finishAt = now + 7 days`. Passes the `totalKernelStaked == 0` guard.
2. After 1 day, Alice calls `initiateWithdrawal(totalStake)` → `updateReward` fires: `rewardPerTokenStored` is updated, `updatedAt = now`, `totalKernelStaked = 0`.
3. Alice waits `withdrawalDelay` and calls `claimWithdrawal` — she correctly receives her principal.
4. For the remaining 6 days, every call to `rewardPerToken()` returns `rewardPerTokenStored` unchanged because `totalKernelStaked == 0`.
5. At `finishAt`, `600_000e18` reward tokens remain in the contract. No function in `KernelDepositPool` can retrieve them. They are permanently locked.

**Foundry test sketch:**
```solidity
function test_rewardsLockedOnZeroStake() public {
    vm.prank(admin);
    pool.setRewardsDuration(7 days);
    kernelToken.mint(alice, 1000e18);
    vm.prank(alice); pool.stake(1000e18);
    rewardsToken.mint(admin, 700_000e18);
    vm.prank(admin); pool.notifyRewardAmount(700_000e18);

    vm.warp(block.timestamp + 1 days);
    vm.prank(alice); pool.initiateWithdrawal(1000e18);
    // totalKernelStaked == 0 from here

    vm.warp(block.timestamp + 7 days); // past finishAt
    // 600_000e18 reward tokens are in the contract
    assertEq(rewardsToken.balanceOf(address(pool)), 600_000e18);
    // No function exists to recover them
}
```