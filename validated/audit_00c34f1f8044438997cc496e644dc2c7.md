Audit Report

## Title
Synthetix-Style Reward Rounding Permanently Destroys Unclaimed Yield via Unconditional `updatedAt` Advancement - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary

`KernelDepositPool` unconditionally advances `updatedAt` to `lastTimeRewardApplicable()` in the `updateReward` modifier on every call to `getReward()`, `stake()`, and `initiateWithdrawal()`. When the elapsed time is short enough that `rewardRate * timeDelta * 1e18 < totalKernelStaked`, the integer division in `rewardPerToken()` truncates to zero, `rewardPerTokenStored` does not increase, yet `updatedAt` advances past the interval — permanently destroying the rewards that should have accrued during it. Any unprivileged caller can accelerate this loss by calling `getReward()` every block.

## Finding Description

**Root cause — unconditional `updatedAt` advancement:**

```solidity
// KernelDepositPool.sol L232-234
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    updatedAt = lastTimeRewardApplicable();   // always advances
```

**Truncation in `rewardPerToken()`:**

```solidity
// KernelDepositPool.sol L412-413
return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / totalKernelStaked;
```

When `rewardRate * timeDelta * 1e18 < totalKernelStaked`, the expression evaluates to zero. `rewardPerTokenStored` is unchanged, but `updatedAt` is written to `block.timestamp`. The reward tokens that should have been credited during `[old_updatedAt, block.timestamp]` are permanently unrecoverable — no future call can reclaim them because the time window has been consumed.

**Concrete truncation condition:** With a reward token of 6 decimals (e.g., USDC), `rewardRate = 83,333` (≈216,000 USDC over 30 days), and `totalKernelStaked = 1e24` (1 million KERNEL), `rewardRate * 12 * 1e18 = 1e24 = totalKernelStaked` — exactly at the truncation boundary. Slightly larger stake or smaller reward rate causes every per-block update to yield zero increment.

**Exploit path:**
1. Admin calls `notifyRewardAmount()` — sets `rewardRate`, `updatedAt = block.timestamp`.
2. Alice stakes a small amount; Eve stakes a large amount or holds no stake.
3. Eve calls `getReward()` at block N. `updateReward` fires: `rewardPerToken()` increment truncates to 0; `rewardPerTokenStored` unchanged; `updatedAt` = block N timestamp. Rewards for that 12-second window are permanently lost.
4. Eve repeats every block. Alice's `earned()` = `smallBalance * 0 / 1e18 + 0` = 0 indefinitely.
5. Eve (as a large staker) is economically incentivized: her own `earned()` = `largeBalance * delta / 1e18` does not truncate, so she collects her rewards while destroying small stakers' yield.

**Why existing checks fail:** There is no guard preventing `updatedAt` from advancing when the computed increment is zero. The `nonReentrant` modifier prevents reentrancy but does not prevent repeated external calls across blocks. `getReward()` has no access control, no minimum time gate, and no check that `rewards[msg.sender] > 0` before executing the modifier.

## Impact Explanation

**Medium — Permanent freezing of unclaimed yield.** Rewards accrued during each griefed block interval are permanently locked in the contract and can never be claimed by any user. The total reward tokens deposited via `notifyRewardAmount()` exceed what can ever be distributed, creating a permanent shortfall. This matches the allowed impact "Medium. Permanent freezing of unclaimed yield."

## Likelihood Explanation

`getReward()` is a public, permissionless function requiring no stake, no role, and no prior relationship with the protocol. A large staker has direct economic incentive to call it every block: they collect their own rewards normally (large balance compensates for small per-token delta) while small stakers receive zero. On Ethereum mainnet, the gas cost per call is non-trivial but automatable, and the economic gain from denying competitors' yield can outweigh it when the reward pool is large. The truncation condition is realistic for reward tokens with fewer than 18 decimals or when total staked supply is large relative to the reward rate.

## Recommendation

1. **Do not advance `updatedAt` when the increment is zero:** Only write `updatedAt = lastTimeRewardApplicable()` when `rewardPerToken()` actually increases. If the increment truncates to zero, leave `updatedAt` unchanged so the elapsed time carries forward to the next call.
2. **Use a higher-precision accumulator:** Scale `rewardPerTokenStored` by `1e36` instead of `1e18` to reduce the likelihood of truncation under realistic parameters.
3. **Add a minimum update interval:** Require `block.timestamp - updatedAt >= MIN_UPDATE_INTERVAL` before allowing `updateReward` to advance the timestamp, preventing per-block griefing.

## Proof of Concept

**Foundry invariant/fork test plan:**

```solidity
// Setup:
// - rewardToken: 6-decimal token (USDC-like)
// - rewardRate set so rewardRate * 12 * 1e18 <= totalKernelStaked
// - Alice stakes smallAmount (e.g., 1e15 KERNEL wei)
// - Eve stakes largeAmount (e.g., 9.99e23 KERNEL wei)
// - totalKernelStaked = 1e24 (1 million KERNEL)
// - rewardRate = 83333 (USDC units/sec, ~216k USDC over 30 days)

// Attack loop (run for N blocks):
for (uint i = 0; i < N; i++) {
    vm.roll(block.number + 1);
    vm.warp(block.timestamp + 12);
    vm.prank(eve);
    pool.getReward(); // triggers updateReward, truncates increment to 0, advances updatedAt
}

// Assertions:
// 1. pool.earned(alice) == 0  (Alice accrued nothing)
// 2. rewardsToken.balanceOf(address(pool)) > 0  (rewards locked in contract)
// 3. pool.rewardPerTokenStored == initialRewardPerTokenStored  (never increased)
// 4. Total claimable by all users < rewardRate * elapsed  (permanent shortfall)
```

The invariant `sum(earned(user) for all users) + rewardsToken.balanceOf(pool) == initial_reward_deposit` will be violated, demonstrating permanent yield destruction.