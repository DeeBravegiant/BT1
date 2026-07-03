Audit Report

## Title
Reward Tokens Permanently Locked When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
In `KernelDepositPool`, when all stakers withdraw during an active reward distribution window, `totalKernelStaked` reaches zero. The `rewardPerToken()` function short-circuits and returns the stored value unchanged, but the `updateReward` modifier still advances `updatedAt` to the current time. Reward tokens that accrued during the zero-stake gap are never distributed to anyone and are permanently locked in the contract with no recovery mechanism.

## Finding Description
The `rewardPerToken()` function at L408–414 guards against division by zero by returning `rewardPerTokenStored` unchanged when `totalKernelStaked == 0`:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;  // unchanged
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

The `updateReward` modifier at L232–242, applied to every state-changing function, unconditionally advances `updatedAt = lastTimeRewardApplicable()` regardless of whether `totalKernelStaked` is zero:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    updatedAt = lastTimeRewardApplicable();   // always advances
    ...
}
```

When the last staker calls `initiateWithdrawal` (L320–338), the modifier runs with the current non-zero `totalKernelStaked`, correctly checkpointing `rewardPerTokenStored` and setting `updatedAt = now`. After that call, `totalKernelStaked` becomes 0. For any subsequent time elapsed while `totalKernelStaked == 0`, `rewardRate` continues to tick but `rewardPerTokenStored` never increases. When the next staker calls `stake`, the modifier again sets `updatedAt = lastTimeRewardApplicable()`, permanently skipping the entire zero-stake interval. The reward tokens for that interval remain in the contract balance but are unclaimable by anyone.

The `notifyRewardAmount` guard at L570 only prevents *starting* a new period with zero stake (`if (totalKernelStaked == 0) revert NoStakedTokens()`), but does not prevent all tokens from being unstaked *after* a period has started. There is no `sweep`, `recoverTokens`, or any other function in the contract that could retrieve the stranded reward tokens.

The contract's own NatSpec at L17–23 explicitly acknowledges this behavior and states the mitigation is purely operational ("ensuring there are always some tokens staked"), with no code-level enforcement.

## Impact Explanation
Reward tokens (the `rewardsToken` ERC-20) accumulate in the `KernelDepositPool` contract and are permanently unclaimable. There is no admin rescue function. The magnitude equals `rewardRate × (duration of zero-stake gap)`, which can be a substantial fraction of the entire reward budget. This matches **Medium: Permanent freezing of unclaimed yield**.

## Likelihood Explanation
Any staker can call `initiateWithdrawal` at any time without restriction during an active reward period. No attacker collusion is required; ordinary user behavior (e.g., a coordinated or organic full exit in response to a market event or better yield opportunity) is sufficient to trigger the loss. The `withdrawalDelay` only delays the return of staked principal, not the act of reducing `totalKernelStaked`, which happens immediately at `initiateWithdrawal` call time (L326).

## Recommendation
1. **Track "dead time" explicitly**: When `totalKernelStaked` drops to zero, record the timestamp. When it becomes non-zero again, advance `updatedAt` only to that recorded timestamp, not to the current time, so the zero-stake interval is excluded from the reward window rather than silently skipped.
2. **Alternatively**, add a `recoverUnallocatedRewards()` admin function that computes the unallocated balance (`rewardsToken.balanceOf(address(this)) - sum_of_all_earned_rewards`) and sends it to the treasury.
3. For the integer-division dust in `notifyRewardAmount` (`receivedAmount % duration` tokens discarded per call): accumulate the remainder and carry it forward into the next `notifyRewardAmount` call.

## Proof of Concept
1. Admin calls `notifyRewardAmount(1_000e18)` with `duration = 30 days`; `rewardRate = 1_000e18 / (30 days)`.
2. Alice is the only staker: `balanceOf[alice] = 100e18`, `totalKernelStaked = 100e18`.
3. After 10 days, Alice calls `initiateWithdrawal(100e18)`. The modifier checkpoints correctly; `totalKernelStaked` becomes 0. Alice has earned ~333e18 reward tokens.
4. 10 days pass with no stakers. `rewardRate` ticks but `rewardPerTokenStored` is frozen.
5. Bob calls `stake(1e18)`. The modifier runs: `rewardPerToken()` returns `rewardPerTokenStored` (unchanged because `totalKernelStaked` was 0), then sets `updatedAt = now`, permanently skipping the 10-day gap.
6. The ~333e18 reward tokens that accrued during the 10-day zero-stake window are now permanently stranded in the contract. Alice earned ~333e18 (days 0–10). Bob earns only ~333e18 (days 20–30). The middle third is locked forever with no recovery path.

**Foundry test plan**: Deploy `KernelDepositPool`, call `notifyRewardAmount`, warp 10 days, call `initiateWithdrawal` for the sole staker, warp another 10 days, call `stake` for a new staker, warp to end of period, call `getReward` for both users, assert that `rewardsToken.balanceOf(address(pool))` still holds ~333e18 tokens that neither user can claim.