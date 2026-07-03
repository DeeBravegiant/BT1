Audit Report

## Title
Rewards Permanently Lost When `totalKernelStaked` Drops to Zero During an Active Reward Period - (File: `contracts/KERNEL/KernelDepositPool.sol`)

## Summary
`KernelDepositPool` implements a Synthetix-style staking reward model where `rewardPerToken()` stops accumulating whenever `totalKernelStaked == 0`. Because `initiateWithdrawal` decrements `totalKernelStaked` immediately at initiation rather than at claim time, a full exit by all stakers during an active reward window causes all remaining allocated rewards (`rewardRate * remainingTime`) to become permanently unclaimable with no on-chain recovery path.

## Finding Description
`rewardPerToken()` (L408–414) returns `rewardPerTokenStored` unchanged when `totalKernelStaked == 0`, silently skipping elapsed time:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

`initiateWithdrawal` (L325–326) immediately decrements both `balanceOf` and `totalKernelStaked` at initiation, not at claim time:

```solidity
balanceOf[msg.sender] -= _amount;
totalKernelStaked -= _amount;
```

The `updateReward` modifier (L232–242) runs before the decrement, so `updatedAt` is set to the current timestamp at the moment of the last withdrawal. From that point forward, with `totalKernelStaked == 0`, `rewardPerToken()` freezes and no further rewards accumulate for anyone.

The only guard against this is in `notifyRewardAmount` (L570), which prevents *starting* a reward period with zero stakers:

```solidity
if (totalKernelStaked == 0) revert NoStakedTokens();
```

There is no guard against `totalKernelStaked` dropping to zero *after* a period has started. Furthermore, this same guard in `notifyRewardAmount` prevents the admin from rolling stuck rewards into a new period (since calling it requires `totalKernelStaked > 0`, and even if someone re-stakes before `finishAt`, the rewards lost during the zero-staked window are not separately tracked — only `(finishAt - block.timestamp) * rewardRate` is rolled forward, which does not recover the already-skipped accumulation). No sweep or recovery function exists anywhere in the contract.

The contract's own NatSpec (L18–22) acknowledges this behavior and relies solely on an off-chain operational commitment ("ensuring there are always some tokens staked … for the entire duration of the reward period"), which is not enforced at the contract level.

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.** Reward tokens already transferred into the contract via `notifyRewardAmount` and allocated via `rewardRate` for the remaining period become permanently unclaimable. The `rewardsToken` balance in the contract will exceed the total claimable amount with no mechanism to recover the difference. This matches the allowed impact: *Medium. Permanent freezing of unclaimed yield.*

## Likelihood Explanation
Any staker can call `initiateWithdrawal` at any time for any portion of their balance — there is no lock-up preventing full exit during an active reward period. The `MAX_WITHDRAWAL_DELAY` of 30 days (L35) only delays the token transfer; `totalKernelStaked` is decremented immediately at initiation. A coordinated or organic mass exit (e.g., a better yield opportunity, a market event, or loss of confidence) can drive `totalKernelStaked` to zero mid-period. No privileged access or special conditions are required beyond normal user withdrawals.

## Recommendation
1. **Checkpoint and pause the reward rate**: When `totalKernelStaked` reaches zero inside `initiateWithdrawal`, store the unallocated rewards (`(finishAt - block.timestamp) * rewardRate`) and set `rewardRate = 0` and `finishAt = block.timestamp`. When `notifyRewardAmount` is next called, add the stored amount to `receivedAmount` so it is rolled into the new period.
2. **Revert on last-staker exit during active period**: Add a check in `initiateWithdrawal` that reverts if `totalKernelStaked - _amount == 0 && block.timestamp < finishAt`, forcing the last staker to wait until the reward period ends before fully exiting.
3. **Carry-forward accounting**: Track a `stuckRewards` state variable incremented whenever `totalKernelStaked` hits zero mid-period, and include it in the next `notifyRewardAmount` calculation.

## Proof of Concept
1. Admin calls `notifyRewardAmount(1_000e18)` with `duration = 7 days`. `rewardRate ≈ 1653 tokens/sec`, `finishAt = T + 7 days`.
2. At `T + 1 day`, all stakers call `initiateWithdrawal(fullBalance)`. The `updateReward` modifier runs, setting `updatedAt = T + 1 day`. Then `totalKernelStaked` becomes `0`.
3. For the remaining 6 days (`T+1d` to `T+7d`), every call to `rewardPerToken()` returns `rewardPerTokenStored` unchanged — no rewards accumulate.
4. Rewards permanently lost ≈ `1653 * 6 * 86400 ≈ 857,145,600` token-wei (≈ 857 tokens), stuck in the contract.
5. No function in the contract can recover these tokens.

**Foundry test plan:**
```solidity
function test_rewardsStuckOnZeroStake() public {
    // Setup: stake, start reward period
    vm.prank(admin);
    pool.notifyRewardAmount(1_000e18); // 7-day period

    // Fast-forward 1 day, then all stakers withdraw
    vm.warp(block.timestamp + 1 days);
    vm.prank(staker);
    pool.initiateWithdrawal(stakerBalance);
    assertEq(pool.totalKernelStaked(), 0);

    // Fast-forward to end of reward period
    vm.warp(block.timestamp + 6 days);

    // Verify rewardPerToken did not change after zero-stake point
    uint256 rpt = pool.rewardPerToken();
    // rpt should equal the value at T+1d, not T+7d
    // Verify contract holds more rewardsToken than can ever be claimed
    uint256 contractBalance = rewardsToken.balanceOf(address(pool));
    uint256 claimable = pool.earned(staker);
    assertGt(contractBalance, claimable); // stuck rewards confirmed
}
```