Audit Report

## Title
Reward Precision Loss Permanently Freezes Unclaimed Yield in `KernelDepositPool` — (File: `contracts/KERNEL/KernelDepositPool.sol`)

## Summary
`KernelDepositPool` suffers two independent integer-division truncation bugs that permanently lock reward tokens in the contract. First, `notifyRewardAmount` discards `receivedAmount % duration` tokens every reward period with no recovery path. Second, `rewardPerToken()` can produce a zero increment when `rewardRate * timeDelta * 1e18 < totalKernelStaked`, yet `updatedAt` is always advanced, silently discarding the rewards for that window. Both losses are permanent and unconditional under realistic protocol conditions.

## Finding Description

**Root cause 1 — `notifyRewardAmount` truncation (L579–583):**

```solidity
if (block.timestamp >= finishAt) {
    rewardRate = receivedAmount / duration;   // remainder discarded
} else {
    uint256 remaining = (finishAt - block.timestamp) * rewardRate;
    rewardRate = (receivedAmount + remaining) / duration;  // remainder discarded
}
```

`receivedAmount % duration` tokens are transferred into the contract but never allocated to any staker. There is no `undistributed` accumulator and no admin sweep function. The remainder is silently stranded on every call to `notifyRewardAmount`.

**Root cause 2 — `rewardPerToken()` zero-increment with `updatedAt` advancement (L408–414, L232–242):**

```solidity
return rewardPerTokenStored
    + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / totalKernelStaked;
```

When `rewardRate * timeDelta * 1e18 < totalKernelStaked`, the division truncates to zero. The `updateReward` modifier unconditionally writes `updatedAt = lastTimeRewardApplicable()` regardless of whether the accumulator advanced. The rewards that should have accrued during `timeDelta` are permanently unallocatable — no staker can ever claim them.

**Why existing checks are insufficient:**
- The `rewardRate == 0` revert in `notifyRewardAmount` (L586) only catches a fully-zero rate; it does not prevent truncation of the remainder.
- The `totalKernelStaked == 0` guard in `rewardPerToken()` (L409) only handles the zero-stake edge case; it does not prevent rounding to zero when stake is large relative to `rewardRate`.
- There is no minimum `rewardRate * 1e18 / totalKernelStaked >= 1` invariant enforced anywhere.

## Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

Reward tokens transferred into the contract become permanently unclaimable:

1. Every call to `notifyRewardAmount` strands up to `duration - 1` wei of the reward token. For a 6-decimal token (e.g., USDC) with a 30-day duration, this is up to `2,591,999` USDC wei (~2.59 USDC) per period, compounding across periods.

2. Every state-changing call during a window where `rewardRate * timeDelta * 1e18 < totalKernelStaked` discards `rewardRate * timeDelta` worth of reward tokens. Under the PoC parameters (385 wei/s rate, 1 M KERNEL staked), any interaction within a ~43-minute window produces a zero increment, permanently losing those rewards.

Both losses are irreversible: no admin function exists to redistribute stranded tokens, and the accumulator cannot retroactively recover skipped windows.

## Likelihood Explanation

**Medium.**

The `notifyRewardAmount` truncation is unconditional — it fires on every reward period regardless of any attacker action or protocol state.

The `rewardPerToken()` rounding condition is reachable under realistic live-protocol conditions without any attacker:
- A 6-decimal reward token (USDC, USDT) is a plausible choice for a staking rewards contract.
- With 1,000 USDC over 30 days: `rewardRate = 385` wei/s.
- With 1,000,000 KERNEL (18 decimals) staked: `totalKernelStaked = 1e24`.
- Per-second increment: `385 * 1 * 1e18 / 1e24 = 0`. Any transaction within a ~43-minute window produces a zero increment.
- In a live protocol with normal user activity (stakes, withdrawals, claims), sub-43-minute transaction intervals are routine, making this loss continuous and compounding.

## Recommendation

1. **`notifyRewardAmount` truncation**: Accumulate the undistributed remainder and roll it into the next period:
   ```solidity
   uint256 leftover = (block.timestamp < finishAt)
       ? (finishAt - block.timestamp) * rewardRate
       : 0;
   rewardRate = (receivedAmount + leftover) / duration;
   // optionally: undistributed += (receivedAmount + leftover) % duration;
   ```

2. **`rewardPerToken()` zero-increment**: Enforce a minimum viable rate in `notifyRewardAmount`:
   ```solidity
   require(rewardRate * DECIMAL_PRECISION / totalKernelStaked >= 1, "RewardRateTooLow");
   ```
   Alternatively, use a higher internal precision (e.g., `1e36`) for the accumulator and scale down only in `earned()`.

3. **Reward token decimals**: Document or enforce (via `initialize`) that the reward token must have at least 18 decimals, or apply a decimal-normalization factor in `rewardPerToken()`.

## Proof of Concept

**Scenario A — `notifyRewardAmount` truncation (no attacker required):**
```
duration = 2,592,000 (30 days)
receivedAmount = 1,000e6 (1,000 USDC)
rewardRate = 1,000e6 / 2,592,000 = 385
Stranded = 1,000e6 - 385 * 2,592,000 = 1,888,000 USDC wei (≈1.888 USDC, permanent)
```
Repeat for N periods → N * ~1.888 USDC permanently locked.

**Scenario B — `rewardPerToken()` zero-increment under normal usage:**
```
rewardRate = 385, totalKernelStaked = 1,000,000e18 = 1e24
Threshold timeDelta for non-zero increment: 385 * t * 1e18 >= 1e24 → t >= 2597 seconds (~43 min)

1. Admin calls notifyRewardAmount → rewardRate = 385, finishAt = now + 30 days
2. User A calls stake() at T=0
3. User B calls stake() at T=60 (1 minute later)
   → updateReward: rewardPerToken increment = 385 * 60 * 1e18 / 1e24 = 0
   → rewardPerTokenStored unchanged, updatedAt advanced by 60 seconds
   → 385 * 60 = 23,100 USDC wei of rewards permanently lost
4. Repeat for every transaction within 43-minute windows throughout the 30-day period
```

**Foundry invariant test plan:**
```solidity
function invariant_rewardTokensAccountedFor() public {
    uint256 contractBalance = rewardsToken.balanceOf(address(pool));
    uint256 allocatable = pool.rewardRate() * (pool.finishAt() - block.timestamp);
    uint256 alreadyEarned = /* sum of earned() for all stakers */;
    // Invariant: contractBalance >= allocatable + alreadyEarned
    // Violation proves stranded tokens exist
    assertGe(contractBalance, allocatable + alreadyEarned);
}
```
A fuzz run with low-decimal reward tokens and frequent `stake(1)` calls will break this invariant, demonstrating permanent yield loss.