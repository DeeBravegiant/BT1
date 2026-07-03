Audit Report

## Title
Insufficient Precision in `rewardRate` Causes Permanent Freezing of Unclaimed Yield - (File: `contracts/KERNEL/KernelDepositPool.sol`)

## Summary

`KernelDepositPool.sol` stores `rewardRate` as raw token-units-per-second with no scaling multiplier. When a low-decimal reward token (e.g., USDC, 6 decimals) is used alongside a large `totalKernelStaked`, the `rewardPerToken()` numerator truncates to zero for short time deltas. Because the `updateReward` modifier unconditionally advances `updatedAt` on every user action regardless of whether `rewardPerTokenStored` changed, the time window is permanently consumed and the corresponding reward tokens are irrecoverably locked in the contract.

## Finding Description

**Root cause — `notifyRewardAmount()` line 580:**

```solidity
rewardRate = receivedAmount / duration;
```

`rewardRate` is stored as raw token-units-per-second with no scaling multiplier. The remainder `receivedAmount % duration` is silently discarded and permanently locked — no `recoverERC20` or equivalent exists in the contract.

**Compounding precision loss — `rewardPerToken()` lines 412–413:**

```solidity
return rewardPerTokenStored
    + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / totalKernelStaked;
```

`DECIMAL_PRECISION = 1e18`. With a 6-decimal reward token and large `totalKernelStaked`, the numerator `rewardRate * Δt * 1e18` is smaller than `totalKernelStaked` for short intervals, causing the entire expression to truncate to **zero**.

Concrete verification with PoC parameters:
- `rewardRate = 1_000_000_000 / 604_800 = 1653`
- `totalKernelStaked = 1e24`
- For `Δt = 1s`: numerator = `1653 * 1 * 1e18 = 1.653e21` < `1e24` → result = **0**
- Minimum `Δt` for non-zero result: `1e24 / (1653 * 1e18) ≈ 605 seconds`

Any user interaction within ~605 seconds of the last `updateReward` call produces a zero delta.

**Critical interaction — `updateReward` modifier lines 232–241:**

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();   // unchanged (zero delta)
    updatedAt = lastTimeRewardApplicable();    // ADVANCES unconditionally
    ...
}
```

`updatedAt` advances to `lastTimeRewardApplicable()` regardless of whether `rewardPerTokenStored` increased. The time window is consumed but no `rewardPerTokenStored` increase is recorded. The rewards accrued during that interval are permanently unrecoverable.

This modifier fires on every public user action: `stake()` (line 281), `stakeFor()` (line 296), `initiateWithdrawal()` (line 320), and `getReward()` (line 382). `claimWithdrawal()` does not call `updateReward`, but the other four functions are sufficient to trigger the loss repeatedly.

## Impact Explanation

**Medium. Permanent freezing of unclaimed yield.**

Every call to any state-changing function by any unprivileged user triggers `updateReward`, which advances `updatedAt` without increasing `rewardPerTokenStored` whenever the per-second accrual rounds to zero. The reward tokens transferred into the contract by the admin via `notifyRewardAmount()` remain locked with no recovery path. The fraction lost scales with interaction frequency and the ratio `totalKernelStaked / (rewardRate × 1e18)`. This is not a hypothetical edge case — it is the normal operating condition for a 6-decimal reward token with realistic TVL.

## Likelihood Explanation

- `KernelDepositPool` accepts an arbitrary `rewardsToken` set at `initialize()` with no decimal restriction; USDC/USDT (6 decimals) are common and realistic choices.
- `totalKernelStaked` grows naturally as users stake; 1 million KERNEL (1e24 units, 18 decimals) is a realistic TVL.
- Every user interaction (`stake`, `initiateWithdrawal`, `getReward`) triggers `updateReward`, making frequent rounding-to-zero inevitable in an active pool.
- No admin action can prevent this once the reward period is live; the only mitigation would be to prevent all user interactions, which defeats the protocol's purpose.
- No special attacker capability is required — normal user behavior is sufficient.

## Recommendation

Scale `rewardRate` by a precision multiplier (e.g., `1e27`) in `notifyRewardAmount()`:

```solidity
uint256 internal constant RATE_PRECISION = 1e27;

// in notifyRewardAmount():
rewardRate = receivedAmount * RATE_PRECISION / duration;

// in rewardPerToken():
return rewardPerTokenStored
    + (rewardRate * (lastTimeRewardApplicable() - updatedAt))
    / totalKernelStaked;

// in earned():
return (balanceOf[_account]
    * (rewardPerToken() - userRewardPerTokenPaid[_account])
    / RATE_PRECISION)
    + rewards[_account];
```

This eliminates both the `notifyRewardAmount` truncation remainder and the `rewardPerToken` rounding-to-zero problem.

## Proof of Concept

**Setup:**
- `rewardsToken` = USDC (6 decimals)
- `receivedAmount` = 1,000 USDC = `1_000_000_000` (1e9 raw units)
- `duration` = 7 days = 604,800 seconds
- `totalKernelStaked` = 1,000,000 KERNEL = `1e24` (18 decimals)

**Step 1 — `rewardRate` truncation:**
```
rewardRate = 1_000_000_000 / 604_800 = 1653
Distributed = 1653 × 604_800 = 999_734_400
Permanently stuck = 1_000_000_000 − 999_734_400 = 265_600 (≈ 0.27 USDC)
```

**Step 2 — `rewardPerToken()` rounding to zero:**
```
For Δt = 1s:
  numerator = 1653 * 1 * 1e18 = 1.653e21
  denominator = 1e24
  result = 0  (truncated)

Minimum Δt for non-zero result ≈ 605 seconds
```

**Step 3 — `updateReward` consumes the time window:**
```
Alice calls stake() at T=0  → updatedAt = T=0
Bob  calls stake() at T=300 → rewardPerToken delta = 0, updatedAt = T=300
                               Rewards for [T=0, T=300] permanently lost
Alice calls stake() at T=600 → rewardPerToken delta = 0, updatedAt = T=600
                               Rewards for [T=300, T=600] permanently lost
```

**Foundry test plan:**
1. Deploy `KernelDepositPool` with USDC as `rewardsToken` and a mock 18-decimal KERNEL token.
2. Stake 1e24 KERNEL tokens.
3. Call `notifyRewardAmount(1_000_000_000)` with `duration = 604_800`.
4. Warp forward 300 seconds; call `stake(1)` (triggers `updateReward`).
5. Warp forward another 300 seconds; call `getReward()`.
6. Assert that `rewards[user]` is 0 despite 600 seconds of elapsed reward time, and that the reward tokens remain locked in the contract.