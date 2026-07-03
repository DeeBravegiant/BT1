Audit Report

## Title
Reward tokens permanently frozen when `totalKernelStaked` drops to zero during an active reward period - (File: `contracts/KERNEL/KernelDepositPool.sol`)

## Summary

`KernelDepositPool` unconditionally advances `updatedAt` to `lastTimeRewardApplicable()` in the `updateReward` modifier even when `totalKernelStaked == 0`. Because `rewardPerToken()` returns `rewardPerTokenStored` unchanged when no tokens are staked, any time elapsed while the pool is empty consumes the reward timeline without distributing tokens. Those tokens remain in the contract with no recovery path. The contract's own NatSpec at lines 17–22 acknowledges this behavior but relies solely on off-chain operational controls, not on-chain enforcement.

## Finding Description

**Root cause:** In `updateReward` (L232–242), `updatedAt = lastTimeRewardApplicable()` executes unconditionally:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();   // returns stored value unchanged when totalKernelStaked == 0
    updatedAt = lastTimeRewardApplicable();    // always advances
    ...
}
```

`rewardPerToken()` (L408–414) short-circuits when `totalKernelStaked == 0`, returning `rewardPerTokenStored` without accumulating anything. The combination means the time window is consumed but no rewards are credited.

**Exploit flow:**

1. Admin calls `notifyRewardAmount(amount)` → `rewardRate = amount / duration`, `finishAt = now + duration`, `updatedAt = now`. Requires `totalKernelStaked > 0` (L570 guard passes).
2. All stakers call `initiateWithdrawal`. On the last withdrawal, `updateReward` runs while `totalKernelStaked > 0` (correct snapshot), then `totalKernelStaked` becomes 0 (L326).
3. Time passes. `rewardRate` implies ongoing emission, but no accumulation occurs.
4. A new staker calls `stake`. `updateReward(newStaker)` runs with `totalKernelStaked == 0`:
   - `rewardPerToken()` → `rewardPerTokenStored` (unchanged)
   - `updatedAt = lastTimeRewardApplicable()` → advances past the entire gap
   - `totalKernelStaked += _amount` happens after the modifier (L285), so the gap is already consumed
5. Rewards equal to `rewardRate × T_gap` are permanently unaccounted for.

**Why existing guards fail:**

- The `notifyRewardAmount` guard (`if (totalKernelStaked == 0) revert NoStakedTokens()`, L570) only blocks starting a new period with zero stakers. It does not prevent mid-period drain.
- When the admin later calls `notifyRewardAmount` again, the branch at L579–580 (`rewardRate = receivedAmount / duration`) silently discards the frozen tokens.
- There is no `recoverERC20` or equivalent function anywhere in the contract.

The contract's own NatSpec (L17–22) explicitly acknowledges this: *"If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards for that period will stay locked in the contract."* The stated mitigation is purely operational ("ensuring there are always some tokens staked… for the entire duration of the reward period"), with zero on-chain enforcement.

## Impact Explanation

**Medium — Permanent freezing of unclaimed yield.** Reward tokens deposited by the admin for distribution to stakers become permanently locked in the contract. The exact amount frozen is `rewardRate × T_gap`, where `T_gap` is the duration during which `totalKernelStaked == 0` within an active reward period. These tokens cannot be distributed to any staker and cannot be recovered by the admin. This matches the allowed impact class "Permanent freezing of unclaimed yield."

## Likelihood Explanation

Any staker can call `initiateWithdrawal` at any time — it is an unprivileged, externally reachable function. A single address holding 100% of staked KERNEL can trigger this unilaterally. Even without a dominant holder, all stakers legitimately exiting during a reward period (e.g., due to market conditions) produces the same outcome. No attacker capability beyond normal staking/withdrawal is required.

## Recommendation

Do not advance `updatedAt` when `totalKernelStaked == 0`. Modify the `updateReward` modifier so the timestamp checkpoint only moves forward when there are tokens staked to absorb the rewards:

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

This ensures the time gap is not consumed when no tokens are staked — rewards for that period remain distributable once stakers return. Alternatively, add an admin `recoverERC20` function that can only withdraw tokens in excess of the currently owed reward balance (`rewardRate × (finishAt - block.timestamp)`).

## Proof of Concept

```
T=0:  Admin calls setRewardsDuration(10 days)
      Admin calls notifyRewardAmount(1000e18)  [Alice is staked, guard passes]
      → rewardRate ≈ 1157 tokens/sec
      → finishAt = T+10d, updatedAt = T

T=1d: Alice stakes 1000 KERNEL (or was already staked)

T=2d: Alice calls initiateWithdrawal(1000)
      updateReward(Alice): totalKernelStaked=1000 > 0
        → rewardPerTokenStored updated for day 1
        → updatedAt = T+2d
        → rewards[Alice] = 1 day of rewards (correctly saved)
      totalKernelStaked → 0

      [T+2d to T+7d: no stakers; rewardRate still emitting ~1157 tokens/sec
       but updatedAt is frozen at T+2d, no accumulation occurs]

T=7d: Bob calls stake(1000)
      updateReward(Bob): totalKernelStaked=0
        → rewardPerToken() returns rewardPerTokenStored (unchanged)
        → updatedAt = lastTimeRewardApplicable() = T+7d  ← 5-day gap consumed
      totalKernelStaked → 1000

T=10d: finishAt reached
       Bob earned:              rewardRate × 3 days ≈ 300e18 tokens
       Alice earned:            rewardRate × 1 day  ≈ 100e18 tokens
       Permanently frozen:      rewardRate × 5 days ≈ 500e18 tokens
       → No function exists to recover or redistribute these 500e18 tokens
```

Foundry invariant test plan: deploy contract, stake, fast-forward, withdraw all, fast-forward, stake again, fast-forward to `finishAt`, assert `rewardsToken.balanceOf(contract) == 0` after all claims — the invariant will fail, proving tokens are frozen.