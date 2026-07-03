Audit Report

## Title
Rewards Permanently Frozen When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: `contracts/KERNEL/KernelDepositPool.sol`)

## Summary
`KernelDepositPool` uses a Synthetix-style reward accumulation model where `rewardPerToken()` freezes when `totalKernelStaked == 0`, but `rewardRate` continues to tick. Any unprivileged staker can call `initiateWithdrawal` at any time, immediately decrementing `totalKernelStaked`. If the last staker exits mid-period, all remaining rewards for that window are permanently locked in the contract with no on-chain recovery path.

## Finding Description
`rewardPerToken()` short-circuits to `rewardPerTokenStored` when `totalKernelStaked == 0`:

```solidity
// contracts/KERNEL/KernelDepositPool.sol L408-414
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

`notifyRewardAmount` guards against starting a period with zero stakers (L570), but this check is point-in-time only. `initiateWithdrawal` immediately decrements `totalKernelStaked` (L325-326) before any delay:

```solidity
// L325-326
balanceOf[msg.sender] -= _amount;
totalKernelStaked -= _amount;
```

The `updateReward(msg.sender)` modifier on `initiateWithdrawal` correctly snapshots the withdrawing user's earned rewards up to that moment, but once `totalKernelStaked` reaches zero, the ongoing `rewardRate * elapsed_time` accrues to no address. The contract has no `sweep`, `rescue`, or `recoverERC20` function. The only egress path for `rewardsToken` is `getReward()`, which requires a non-zero `rewards[account]` — impossible for rewards that accrued during the zero-staked window. The contract's own NatSpec (L17-22) acknowledges this behavior but relies entirely on an off-chain operational strategy (admin ensuring stakers are always present), which is not enforced on-chain.

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.** Reward tokens transferred into the contract via `notifyRewardAmount` that correspond to any time interval where `totalKernelStaked == 0` are permanently unclaimable. No user accumulates them, and no admin function can recover them. This matches the allowed impact: *Medium. Permanent freezing of unclaimed yield.*

## Likelihood Explanation
**Medium.** `initiateWithdrawal` is callable by any unprivileged staker at any time with no preconditions beyond having a staked balance. In a low-participation pool or single-staker scenario, one withdrawal drops `totalKernelStaked` to zero. This is realistic during market stress, low-TVL periods, or when a single large depositor exits. The withdrawal delay does not prevent the accounting freeze — `totalKernelStaked` is decremented at `initiateWithdrawal`, not at `claimWithdrawal`.

## Recommendation
In `initiateWithdrawal`, after decrementing `totalKernelStaked`, checkpoint `updatedAt` to `block.timestamp` if the result is zero. This prevents the frozen interval from being counted against the reward window:

```diff
balanceOf[msg.sender] -= _amount;
totalKernelStaked -= _amount;
+if (totalKernelStaked == 0) {
+    updatedAt = block.timestamp;
+}
```

Alternatively, add an admin-only `recoverUnallocatedRewards()` that can sweep rewards accrued during zero-staked windows after `finishAt`.

## Proof of Concept
```
1. Admin calls notifyRewardAmount(1_000e18) with one staker holding 1_000e18 KERNEL.
   → NoStakedTokens guard passes. rewardRate set, finishAt = block.timestamp + duration.

2. Staker calls initiateWithdrawal(1_000e18).
   → updateReward snapshots earned rewards correctly up to this point.
   → totalKernelStaked = 0.

3. Time advances to finishAt.
   → rewardPerToken() returns frozen rewardPerTokenStored for the entire window.
   → rewardRate * remaining_duration worth of tokens accrued to no address.

4. No address has accumulated the unaccrued rewards.
   → getReward() transfers nothing for any account.
   → No sweep/rescue function exists.
   → Rewards are permanently locked.
```

Foundry test: deploy contract, stake, call `notifyRewardAmount`, call `initiateWithdrawal` for full balance, `warp` to `finishAt`, assert `rewardsToken.balanceOf(contract) > 0` and `earned(staker) < rewardRate * duration`.