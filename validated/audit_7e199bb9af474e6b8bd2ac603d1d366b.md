Audit Report

## Title
Reward tokens permanently locked when `totalKernelStaked` drops to zero during an active reward window - (File: `contracts/KERNEL/KernelDepositPool.sol`)

## Summary
In `KernelDepositPool.sol`, when all stakers call `initiateWithdrawal()` during an active reward window, `totalKernelStaked` immediately drops to zero. The `rewardPerToken()` function freezes accumulation for the zero-staked interval, but the `updateReward` modifier still advances `updatedAt` past that interval. Reward tokens that accrued during the gap are permanently locked in the contract with no recovery path.

## Finding Description
The contract is a Synthetix-style staking rewards contract. The `rewardPerToken()` function at L408–414 returns `rewardPerTokenStored` unchanged when `totalKernelStaked == 0`:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;   // accumulation frozen
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

The `updateReward` modifier at L232–242 unconditionally advances `updatedAt = lastTimeRewardApplicable()` regardless of whether `totalKernelStaked` is zero:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    updatedAt = lastTimeRewardApplicable();   // time always advances
    ...
}
```

`initiateWithdrawal()` at L325–326 immediately decrements `totalKernelStaked` before any time-lock:

```solidity
balanceOf[msg.sender] -= _amount;
totalKernelStaked -= _amount;
```

**Exploit sequence:**
1. Admin calls `notifyRewardAmount(R)` while stakers are present → `rewardRate = R/duration`, `finishAt = now + duration`, `updatedAt = now`. The guard at L570 (`if (totalKernelStaked == 0) revert NoStakedTokens()`) passes because stakers are present.
2. All stakers call `initiateWithdrawal()` → `totalKernelStaked = 0`. The `updateReward` modifier runs, advancing `updatedAt` to the current time and freezing `rewardPerTokenStored`.
3. For the entire zero-staked interval `[t_withdraw, t_restake]`, `rewardPerToken()` returns the frozen `rewardPerTokenStored` while `updatedAt` keeps advancing on every interaction.
4. When a new staker calls `stake()`, `updateReward` runs again: `rewardPerTokenStored` is still the frozen value, but `updatedAt` is now `t_restake`. The `rewardRate × (t_restake - t_withdraw)` tokens that accrued during the gap are never credited to anyone.
5. Those tokens remain as excess balance in the contract. There is no `rescueTokens` or equivalent function.

The `notifyRewardAmount` rollover path (`remaining = (finishAt - block.timestamp) * rewardRate`) does not recover gap tokens — it only accounts for future time-based distribution, not the already-skipped interval.

The contract's own NatSpec at L18–22 explicitly acknowledges this issue but relies entirely on an off-chain operational assumption with no on-chain enforcement:

```
@dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
     for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
     ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
     as well as for the entire duration of the reward period.
```

The existing guard in `notifyRewardAmount` at L570 only prevents starting a reward period with zero stakers; it does not prevent stakers from exiting after a reward period has begun.

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.**

Reward tokens that accrued during a zero-staked interval are irrecoverably locked in the contract. There is no admin rescue function, no rollover mechanism for the gap, and no way for stakers to claim those tokens. The amount lost equals `rewardRate × (duration of zero-staked interval)`. This matches the allowed impact: **Medium. Permanent freezing of unclaimed yield.**

## Likelihood Explanation
**Low/Medium.** The scenario requires all stakers to withdraw during an active reward window. This is realistic because:
- `initiateWithdrawal()` is permissionless for any staker.
- The withdrawal delay only delays token retrieval, not the immediate decrement of `totalKernelStaked`.
- A coordinated or organic exit (e.g., market downturn, better yield elsewhere) can drain all stakers within a single reward period.
- The protocol has no on-chain mechanism to prevent this; the mitigation is purely operational.

## Recommendation
Add an on-chain guard in `initiateWithdrawal()` that prevents `totalKernelStaked` from reaching zero during an active reward window:

```solidity
// Option A: block full withdrawal during active reward window
if (block.timestamp < finishAt && totalKernelStaked - _amount == 0) {
    revert CannotDrainStakeDuringRewardPeriod();
}
```

Alternatively, in `rewardPerToken()`, track a `lostRewards` accumulator when `totalKernelStaked == 0` and roll it into the next `notifyRewardAmount` call. Or add an admin `rescueRewardTokens()` function that can recover excess reward balance (actual balance minus owed rewards).

## Proof of Concept
```solidity
// Setup: duration = 100s, rewardRate = 1e18 tokens/s, 1 staker with 1e18 KERNEL
// T=0:  notifyRewardAmount(100e18) → finishAt=100, updatedAt=0
// T=10: staker calls initiateWithdrawal(1e18)
//   → updateReward runs: rewardPerTokenStored += 1e18*10*1e18/1e18 = 10e18
//   → updatedAt = 10
//   → totalKernelStaked = 0
// T=20: new staker calls stake(1e18)
//   → updateReward runs: rewardPerToken() returns rewardPerTokenStored (10e18, unchanged)
//   → updatedAt = 20   ← gap [10,20] is skipped
//   → totalKernelStaked = 1e18
// T=100: finishAt reached
//   → new staker earned: (100-20)*1e18 = 80e18 tokens
//   → contract holds: 100e18 total - 10e18 (original staker) - 80e18 (new staker) = 10e18 LOCKED
assertEq(rewardsToken.balanceOf(address(pool)), 10e18); // stuck forever
```