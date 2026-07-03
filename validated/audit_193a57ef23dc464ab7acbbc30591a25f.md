Audit Report

## Title
Precision Loss in `rewardPerToken()` Causes Permanent Freezing of Unclaimed Yield With Low-Decimal Reward Tokens - (`contracts/KERNEL/KernelDepositPool.sol`)

## Summary

`KernelDepositPool.rewardPerToken()` computes per-token reward increments using unscaled `rewardRate` (stored as `receivedAmount / duration`). When the reward token has low decimals (e.g., USDC at 6 decimals) and `totalKernelStaked` is large (e.g., 1e24 for 1M KERNEL), the numerator `rewardRate * timeDelta * DECIMAL_PRECISION` is smaller than `totalKernelStaked` for any `timeDelta` under ~43 minutes, causing integer truncation to zero. Every `updateReward` call within that window permanently discards the accrued rewards for that interval, leaving reward tokens locked in the contract with no recovery path.

## Finding Description

`DECIMAL_PRECISION` is `1e18` (L32). `rewardRate` is stored without scaling at `notifyRewardAmount` (L580):

```solidity
rewardRate = receivedAmount / duration;
```

`rewardPerToken()` (L412–413) computes:

```solidity
return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / totalKernelStaked;
```

With USDC (6 decimals), 1000 USDC over 30 days, and 1M KERNEL staked:
- `rewardRate = 1e9 / 2_592_000 = 385`
- For `timeDelta = 60s`: `385 * 60 * 1e18 / 1e24 = 0` (truncated)
- Non-zero threshold: `timeDelta >= 1e24 / (385 * 1e18) ≈ 2597s (~43 min)`

The `updateReward` modifier (L232–242) fires on every `stake()`, `withdraw()`, and `getReward()` call, updating `updatedAt = lastTimeRewardApplicable()` (L234). Each call within the 43-minute window resets `updatedAt` while contributing zero to `rewardPerTokenStored`. The rewards for those intervals are permanently discarded — they are never added to any user's `rewards[_account]` and remain locked in the contract. The `rewardRate == 0` guard at L586 only prevents a zero rate at initialization and does not protect against per-interval truncation.

## Impact Explanation

This matches **Medium: Permanent freezing of unclaimed yield**. Reward tokens transferred into the contract via `notifyRewardAmount` are never distributed to stakers when the truncation condition holds. With frequent user interactions (multiple per hour), nearly the entire reward allocation for a distribution period can be permanently frozen in the contract. There is no admin recovery function to reclaim these tokens.

## Likelihood Explanation

`KernelDepositPool` accepts an arbitrary `rewardsToken` at initialization, making USDC a natural and realistic choice. Any unprivileged user can trigger `updateReward` by calling `stake(1 wei)` or `getReward()` — no special role or coordination is required. With a large staking base (1M+ KERNEL, which is realistic for a protocol-level staking pool), the ~43-minute threshold means ordinary user activity (multiple transactions per hour) is sufficient to cause continuous reward loss without any deliberate attack. The condition is self-reinforcing: more users staking increases `totalKernelStaked`, raising the truncation threshold further.

## Recommendation

Scale `rewardRate` by `DECIMAL_PRECISION` at storage time and remove the scaling factor from `rewardPerToken()`, dividing it back out in `earned()`:

```solidity
// In notifyRewardAmount:
rewardRate = receivedAmount * DECIMAL_PRECISION / duration;

// In rewardPerToken():
return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt))
    / totalKernelStaked;

// In earned():
return (balanceOf[_account] * (rewardPerToken() - userRewardPerTokenPaid[_account]) / DECIMAL_PRECISION)
    + rewards[_account];
```

This ensures the numerator in `rewardPerToken()` retains sufficient precision regardless of the reward token's decimal count.

## Proof of Concept

1. Deploy `KernelDepositPool` with USDC (6 decimals) as `rewardsToken`.
2. Have users stake 1,000,000 KERNEL (1e24 units) so `totalKernelStaked = 1e24`.
3. Admin calls `notifyRewardAmount(1000e6)` with `duration = 30 days`.
   - `rewardRate = 1e9 / 2_592_000 = 385`
4. Any user calls `stake(1 wei)` every 60 seconds, triggering `updateReward`.
   - Each call: `385 * 60 * 1e18 / 1e24 = 0` → `rewardPerTokenStored` unchanged, `updatedAt` reset.
5. After 30 days, all stakers call `getReward()` and receive 0 rewards.
6. 1000 USDC remains permanently locked in the contract.

Foundry test plan: deploy with a mock USDC (6 decimals), stake 1e24 KERNEL, call `notifyRewardAmount(1000e6)`, warp forward 60 seconds and call `stake(1 wei)` in a loop for 30 days, assert `rewardPerTokenStored == 0` and that all stakers receive 0 from `getReward()`.