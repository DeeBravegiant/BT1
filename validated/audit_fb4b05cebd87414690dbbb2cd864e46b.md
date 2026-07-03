Audit Report

## Title
Hardcoded `DECIMAL_PRECISION = 1e18` Causes Permanent Reward Accrual Truncation for Sub-18-Decimal Reward Tokens - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`KernelDepositPool` hardcodes `DECIMAL_PRECISION = 1e18` while accepting an arbitrary `rewardsToken` with no decimal constraint. When a reward token with fewer than 18 decimals (e.g., USDC at 6 decimals) is used and `totalKernelStaked` exceeds `rewardRate * 1e18`, the `rewardPerToken()` increment permanently truncates to zero via integer division, freezing all future unclaimed yield inside the contract with no recovery path.

## Finding Description
`DECIMAL_PRECISION` is declared as a hardcoded constant:

```solidity
// L32
uint256 public constant DECIMAL_PRECISION = 1e18;
```

`rewardsToken` is accepted without any decimal validation in `initialize()`:

```solidity
// L270
rewardsToken = IERC20(_rewardToken);
```

`rewardRate` is stored in the reward token's native units:

```solidity
// L580
rewardRate = receivedAmount / duration;
```

`rewardPerToken()` then scales by the fixed `DECIMAL_PRECISION`:

```solidity
// L412-413
return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / totalKernelStaked;
```

For USDC (6 decimals), `rewardRate` is in units of `1e6/second`. The per-second increment becomes:

```
rewardRate * 1 * 1e18 / totalKernelStaked
= 1e6 * 1e18 / totalKernelStaked
= 1e24 / totalKernelStaked
```

When `totalKernelStaked > 1e24` (i.e., > 1,000,000 KERNEL at 18 decimals), Solidity integer division truncates this to `0`. Once `rewardPerTokenStored` stops increasing, `earned()` at L422 always computes `balanceOf[_account] * 0 / DECIMAL_PRECISION = 0` for the incremental portion, so no new yield accrues for any staker. There is no `recoverERC20` or equivalent function in the contract, so the deposited reward tokens are permanently locked.

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.**

All stakers lose 100% of yield accrued after the truncation threshold is crossed. Reward tokens deposited by the admin via `notifyRewardAmount()` remain permanently locked in the contract. The threshold (e.g., 1M KERNEL for 1 USDC/second reward rate) is easily reachable in normal protocol operation, and no attacker action is required — organic staking growth alone triggers the condition.

## Likelihood Explanation
The `rewardsToken` is freely configurable with no decimal enforcement. USDC is the most common non-18-decimal reward token in DeFi staking. The truncation threshold is a function of `rewardRate * 1e18`; for any realistic reward rate denominated in a 6-decimal token, the threshold is well within expected TVL. No privileged compromise or attacker coordination is needed — any user calling `stake()` can push `totalKernelStaked` past the threshold.

## Recommendation
Replace the hardcoded constant with a value derived from the reward token's decimals at initialization time:

```solidity
uint256 public immutable DECIMAL_PRECISION;

function initialize(address _admin, address _kernelToken, address _rewardToken) external initializer {
    ...
    uint8 rewardDecimals = IERC20Metadata(_rewardToken).decimals();
    DECIMAL_PRECISION = 10 ** rewardDecimals;
    ...
}
```

Alternatively, normalize `rewardRate` to 1e18 precision inside `notifyRewardAmount()` by scaling up by `1e18 / 10**rewardDecimals`, keeping all internal accounting in 1e18 units regardless of the reward token's native decimals.

## Proof of Concept

**Setup:**
- `rewardsToken` = USDC (6 decimals), `kernelToken` = KERNEL (18 decimals)
- `duration` = 2,592,000 seconds (30 days)
- Admin calls `notifyRewardAmount(2_592_000e6)` → `rewardRate = 1e6`
- `totalKernelStaked` = 1,000,001e18 (just over 1M KERNEL, reachable via normal `stake()` calls)

**`rewardPerToken()` increment per second:**
```
1e6 * 1 * 1e18 / 1_000_001e18
= 1e24 / 1_000_001e18
= 0  (integer truncation)
```

**Foundry test plan:**
1. Deploy `KernelDepositPool` with USDC as `rewardsToken`.
2. Stake 1,000,001e18 KERNEL across one or more accounts via `stake()`.
3. Admin calls `notifyRewardAmount(2_592_000e6)`.
4. `vm.warp(block.timestamp + 1 days)`.
5. Assert `rewardPerToken() == rewardPerTokenStored` (no increase).
6. Assert `earned(staker) == 0`.
7. `vm.warp(block.timestamp + 30 days)`.
8. Assert `earned(staker) == 0` and `rewardsToken.balanceOf(address(pool)) == 2_592_000e6` (funds locked).