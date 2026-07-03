Audit Report

## Title
Reward Tokens Permanently Locked When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`KernelDepositPool` uses a Synthetix-style per-token accumulator that freezes when `totalKernelStaked` reaches zero, but does not advance `updatedAt` during the zero-staked window. When a new staker eventually arrives, the `updateReward` modifier silently discards the entire zero-staked interval by setting `updatedAt = lastTimeRewardApplicable()` (current time), permanently locking the reward tokens that `rewardRate` implied for that window inside the contract with no recovery path.

## Finding Description
`rewardPerToken()` (L408–414) returns the frozen `rewardPerTokenStored` whenever `totalKernelStaked == 0`, without advancing `updatedAt`:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;   // accumulator frozen, updatedAt NOT advanced
    }
    ...
}
```

The `updateReward` modifier (L232–242) always sets `updatedAt = lastTimeRewardApplicable()` unconditionally:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    updatedAt = lastTimeRewardApplicable();   // advances even when totalKernelStaked == 0
    ...
}
```

When the last staker calls `initiateWithdrawal` (L320–338), `updateReward` runs first — snapshotting `rewardPerTokenStored` and `updatedAt` at that moment — then the function body sets `totalKernelStaked -= _amount` to zero. From this point, every call to `rewardPerToken()` returns the frozen value. When a new staker later calls `stake()`, `updateReward` fires again: since `totalKernelStaked` is still 0 at modifier execution time, `rewardPerTokenStored` stays frozen, but `updatedAt` is advanced to `lastTimeRewardApplicable()` (current time). The entire zero-staked interval is silently consumed. The reward tokens implied by `rewardRate * elapsed_zero_staked_time` remain in the contract with no mechanism to claim or recover them.

`notifyRewardAmount` (L566–592) does check `if (totalKernelStaked == 0) revert NoStakedTokens()` (L570), but this only prevents starting a new reward period — it does not protect against a mid-period drain to zero, nor does it recover already-locked tokens.

The contract's own NatSpec (L14–23) explicitly acknowledges this behavior and relies entirely on an off-chain operational assumption ("ensuring there are always some tokens staked") that is not enforced on-chain.

## Impact Explanation
Reward tokens emitted by `rewardRate` during the zero-staked window are permanently locked inside `KernelDepositPool`. No user can claim them (no staker exists to accumulate them during that window), and there is no admin rescue or sweep function. This constitutes **permanent freezing of unclaimed yield** — a Medium-severity impact per the allowed scope.

## Likelihood Explanation
Any staker can call `initiateWithdrawal` at any time without restriction. If all stakers exit during an active reward period — a realistic scenario during market stress, a coordinated migration, or loss of confidence — `totalKernelStaked` reaches zero and the remaining period's rewards are locked. No privileged action is required; ordinary user withdrawals are the sole trigger. The scenario requires no attacker: it can occur through independent, individually rational user behavior.

## Recommendation
1. **In `rewardPerToken()`**: when `totalKernelStaked == 0`, also update `updatedAt` to `lastTimeRewardApplicable()` so the zero-staked interval is not silently consumed when stakers return. This prevents future accumulator gaps but does not recover already-locked tokens.
2. **Add an admin recovery function**: allow the admin to reclaim undistributed reward tokens (i.e., `rewardRate * (finishAt - block.timestamp)`) when `totalKernelStaked == 0` and a reward period is active, analogous to the Velodrome pattern of returning claimable rewards to the minter when a gauge is killed.

## Proof of Concept
1. Admin calls `notifyRewardAmount(1_000_000e18)` with `duration = 7 days`. `rewardRate ≈ 1653 tokens/sec`, `finishAt = now + 7 days`.
2. Alice is the sole staker: `balanceOf[Alice] = 1000e18`, `totalKernelStaked = 1000e18`.
3. After 3 days, Alice calls `initiateWithdrawal(1000e18)`. `updateReward` snapshots `rewardPerTokenStored` and sets `updatedAt = now` (day 3). Function body sets `totalKernelStaked = 0`.
4. Days 3–7: every call to `rewardPerToken()` returns the frozen `rewardPerTokenStored`. The ~571,392 tokens implied for the remaining 4 days are in limbo.
5. After `withdrawalDelay`, Alice calls `claimWithdrawal` and `getReward()`, recovering her principal and her 3-day share. The 4-day remainder stays locked.
6. Bob stakes 1 wei on day 5. `updateReward` fires: `totalKernelStaked` is still 0 at modifier time → `rewardPerTokenStored` stays frozen → `updatedAt` advances to day 5. Days 3–5 rewards (~285,696 tokens) are permanently unclaimable. Bob earns only from day 5 onward.

**Foundry test plan**: Deploy `KernelDepositPool`, call `notifyRewardAmount`, have a single staker withdraw after partial period, warp time, have a new staker deposit, then assert that `rewardsToken.balanceOf(address(pool)) > 0` after the period ends and all users have called `getReward()`, confirming tokens are permanently stranded.