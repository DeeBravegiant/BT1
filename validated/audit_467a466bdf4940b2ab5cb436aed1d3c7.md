Audit Report

## Title
`updatedAt` Advances Unconditionally When `totalKernelStaked == 0`, Permanently Freezing Unclaimed Yield — (File: `contracts/KERNEL/KernelDepositPool.sol`)

## Summary

The `updateReward` modifier unconditionally writes `updatedAt = lastTimeRewardApplicable()` even when `totalKernelStaked == 0`. Because `rewardPerToken()` short-circuits and returns `rewardPerTokenStored` unchanged in that state, any elapsed time during a zero-staking window is consumed without distributing rewards. The `rewardRate × elapsed` tokens for that interval are permanently unclaimable, as `updatedAt` has advanced past them and the delta can never be recovered.

## Finding Description

The `updateReward` modifier at lines 232–242 performs two unconditional writes:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();   // (A) no-op when totalKernelStaked == 0
    updatedAt = lastTimeRewardApplicable();    // (B) always advances
    ...
}
```

`rewardPerToken()` at lines 408–414 short-circuits when `totalKernelStaked == 0`, returning `rewardPerTokenStored` unchanged. So step (A) leaves `rewardPerTokenStored` identical, while step (B) advances `updatedAt` to `block.timestamp` (or `finishAt`). The interval `[old_updatedAt, block.timestamp]` is silently consumed: the next call to `rewardPerToken()` computes `(lastTimeRewardApplicable() - updatedAt)` starting from the new, advanced `updatedAt`, so the skipped window is permanently excluded from all future reward calculations.

`initiateWithdrawal` at lines 320–338 is itself guarded by `updateReward` and decrements `totalKernelStaked` in its body (after the modifier runs). Once the last staker withdraws, every subsequent `updateReward`-gated call (`stake`, `initiateWithdrawal`, `getReward`, `notifyRewardAmount`) silently advances `updatedAt` without crediting anyone.

The contract's own NatSpec at lines 18–22 acknowledges this: *"If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards for that period will stay locked in the contract."* The stated mitigation is an off-chain operational assumption (admin ensures tokens are always staked), not an on-chain guard. `notifyRewardAmount` does revert when `totalKernelStaked == 0` at line 570, but this only prevents starting a new period with no stakers — it does not prevent stakers from withdrawing after the period has started, leaving the window unprotected.

## Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

Any `rewardRate × elapsed` tokens accrued during a zero-staking window are permanently locked in the contract. There is no recovery path: `updatedAt` is a monotonically advancing cursor, and once it passes a time interval with `totalKernelStaked == 0`, those tokens can never be claimed by any user. This matches the allowed impact: *"Medium. Permanent freezing of unclaimed yield."*

## Likelihood Explanation

This is reachable by any unprivileged staker through normal contract usage. The last (or only) staker calls `initiateWithdrawal` for their full balance, reducing `totalKernelStaked` to zero. Any subsequent call to a `updateReward`-gated function — including a new staker calling `stake` — will advance `updatedAt` without distributing rewards for the gap. No admin action, privileged role, or exotic precondition is required. The scenario occurs naturally whenever all stakers exit during an active reward period and there is any delay before the next staker joins.

A deliberate griefing amplification is also available: an attacker with 1 wei of KERNEL can call `stake(1)` (advancing `updatedAt` across the zero-staking gap) then `initiateWithdrawal(1)` (resetting `totalKernelStaked` to 0 again) across multiple blocks, progressively advancing `updatedAt` toward `finishAt` and destroying the remaining reward budget. The cost is gas only; the 1-wei principal is recoverable after the withdrawal delay.

## Recommendation

Guard the `updatedAt` write so it only advances when stakers are present:

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

This ensures `updatedAt` only moves forward when there are stakers to absorb the rewards, preventing silent time-window consumption.

## Proof of Concept

```
Setup
─────
• rewardRate  = 1e18 tokens/second
• duration    = 1000 seconds
• Alice stakes 100 KERNEL at T=0
• Admin calls notifyRewardAmount() at T=0
  → finishAt = 1000, updatedAt = 0

Step 1 — Alice withdraws at T=100
  initiateWithdrawal(100) triggers updateReward(Alice):
    totalKernelStaked == 100 → rewardPerToken() = 1e18
    rewardPerTokenStored = 1e18
    updatedAt = 100
    rewards[Alice] = 100 tokens ✓
  totalKernelStaked = 0

Step 2 — Bob stakes 1 wei at T=200
  stake(1) triggers updateReward(Bob):
    totalKernelStaked == 0 → rewardPerToken() returns 1e18 (no change)
    rewardPerTokenStored = 1e18 (unchanged)
    updatedAt = 200  ← silently consumes T=100..200 (100 tokens lost)
  totalKernelStaked = 1

Step 3 — Bob claims at T=300
  getReward() triggers updateReward(Bob):
    rewardPerToken() = 1e18 + (1e18 * (300-200) * 1e18) / 1 = 101e18
    rewards[Bob] = 1 * (101e18 - 1e18) / 1e18 = 100 tokens ✓

Result
──────
• Alice earned 100 tokens (T=0..100)   ✓
• Bob earned   100 tokens (T=200..300) ✓
• 100 tokens for T=100..200 permanently locked (updatedAt jumped 100→200
  with totalKernelStaked==0, consuming that window without distribution)

Foundry invariant test plan:
  invariant: sum of all claimable rewards + contract rewardsToken balance
             >= rewardRate * (finishAt - startAt)
  Fuzz: random stake/initiateWithdrawal/getReward sequences including
        full-withdrawal scenarios that drive totalKernelStaked to 0.
  Expected: invariant breaks whenever a zero-staking gap occurs mid-period.
```