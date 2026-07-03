Audit Report

## Title
Reward Sniping via Frontrunning `notifyRewardAmount` with No Warmup Period — (File: `contracts/KERNEL/KernelDepositPool.sol`)

## Summary
`KernelDepositPool` is a Synthetix-style staking contract where rewards accrue immediately upon staking with no warmup period. An attacker holding KERNEL tokens can stake a large amount before `notifyRewardAmount` is called, capture a disproportionate fraction of the reward stream, and exit after accumulating rewards. When `withdrawalDelay` is uninitialized (defaults to `0`), exit is immediate; even with a non-zero delay the attack remains profitable. The result is direct theft of unclaimed yield from legitimate long-term stakers.

## Finding Description
**Root cause 1 — Immediate reward accrual on stake:**
`stake()` at L281–289 immediately increments `balanceOf[msg.sender]` and `totalKernelStaked`. The `updateReward` modifier at L232–242 snapshots `rewardPerTokenStored` and sets `userRewardPerTokenPaid[msg.sender]` to the current value before the balance change, so the attacker begins earning from the very next second at their full new weight.

**Root cause 2 — `withdrawalDelay` defaults to `0`:**
`initialize()` at L259–271 never assigns `withdrawalDelay`; it remains `0` until the admin explicitly calls `setWithdrawalDelay`. With `withdrawalDelay = 0`, `unlockTime = block.timestamp + 0 = block.timestamp` in `initiateWithdrawal()` at L330, and the `block.timestamp < withdrawal.unlockTime` guard at L355–357 passes immediately, allowing same-block principal recovery.

**Root cause 3 — `notifyRewardAmount` is a public mempool transaction:**
`notifyRewardAmount` at L566–591 is an admin-only call broadcast on-chain. Any mempool watcher can observe it and submit a higher-gas `stake()` to land first in the same block.

**Exploit path:**
1. Attacker monitors the mempool for `notifyRewardAmount`.
2. Attacker submits `stake(large_amount)` with higher gas, landing before `notifyRewardAmount` in the same block. `updateReward` sets `userRewardPerTokenPaid[attacker] = rewardPerTokenStored` (pre-reward value), and `totalKernelStaked` grows to include the attacker's tokens.
3. `notifyRewardAmount` executes: `rewardRate` is set, `updatedAt = block.timestamp`. The attacker's large balance is now part of `totalKernelStaked` for the entire reward window.
4. Over subsequent blocks, `earned(attacker)` accumulates at `attacker_balance / totalKernelStaked × rewardRate` per second — a large fraction if the attacker's stake dominates.
5. Attacker calls `initiateWithdrawal` (requires `maxNumberOfWithdrawalsPerUser > 0`, which the admin must have set) and, when `withdrawalDelay = 0`, immediately calls `claimWithdrawal` to recover principal, then `getReward()` to collect accumulated rewards.

**Existing checks that fail:**
- `notifyRewardAmount` checks `totalKernelStaked == 0` (L570) but does not check whether the staked balance was added in the same block.
- `setWithdrawalDelay` enforces `_withdrawalDelay != 0` (L599) for future updates but cannot prevent the initial `0` default.
- There is no per-user stake timestamp, warmup window, or snapshot mechanism to exclude recently added balances from reward accrual.

**Note on `maxNumberOfWithdrawalsPerUser`:** This variable also defaults to `0`, causing `initiateWithdrawal` to revert (`0 >= 0`) until the admin calls `setMaxNumberOfWithdrawalsPerUser`. The attack therefore requires the admin to have set this value. However, even with a non-zero `withdrawalDelay`, the attack remains profitable as long as accumulated rewards exceed the opportunity cost of the locked capital.

## Impact Explanation
Legitimate stakers who held throughout the reward period receive a smaller share of the reward pool than they are entitled to. The attacker extracts yield that belongs to pre-existing stakers by transiently inflating `totalKernelStaked` at the moment the reward period begins. This is direct **theft of unclaimed yield** — a High-severity impact under the allowed scope.

## Likelihood Explanation
- Requires the attacker to already hold KERNEL tokens (no flash-loan shortcut across blocks), so capital is needed.
- `notifyRewardAmount` is visible in the public mempool before inclusion; frontrunning is straightforward on any chain with a public mempool.
- `withdrawalDelay = 0` by default; the attack is most severe before the admin calls `setWithdrawalDelay`, but remains profitable with any delay shorter than the reward duration.
- No special permissions beyond holding KERNEL tokens are required.
- The attack is repeatable on every new reward period.

**Likelihood: Medium** (capital requirement and mempool visibility are the only constraints).

## Recommendation
1. **Set `withdrawalDelay` to a meaningful non-zero value in `initialize()`** rather than relying on a post-deployment admin call. Similarly, set `maxNumberOfWithdrawalsPerUser` to a safe default in `initialize()`.
2. **Introduce a per-user warmup period:** record `stakeTimestamp[msg.sender]` on each `stake()` call and exclude balances staked within the last `warmupPeriod` seconds from `earned()` / `rewardPerToken()` calculations.
3. **Alternatively, snapshot eligible stakers at `notifyRewardAmount` time:** only balances present before the current block's `notifyRewardAmount` call accrue rewards for that period.

## Proof of Concept
Assume `maxNumberOfWithdrawalsPerUser = 10` (admin has set it), `withdrawalDelay = 0` (uninitialized), `duration = 7 days`, existing `totalKernelStaked = 1_000_000e18`.

1. **Block N, tx 1 (attacker):** `stake(10_000_000e18)`. `updateReward` sets `userRewardPerTokenPaid[attacker] = rewardPerTokenStored` (current, pre-reward value). `totalKernelStaked = 11_000_000e18`. Attacker holds ~90.9% of the pool.
2. **Block N, tx 2 (admin):** `notifyRewardAmount(604_800e18)` → `rewardRate = 1e18/s`, `finishAt = block.timestamp + 7 days`, `updatedAt = block.timestamp`.
3. **Block N+k (e.g., k = 86_400s ≈ 1 day):** Attacker calls `initiateWithdrawal(10_000_000e18)`. `updateReward` snapshots `earned(attacker) ≈ 0.909 × 1e18 × 86_400 ≈ 78_537.6e18` reward tokens. `unlockTime = block.timestamp` (delay = 0).
4. **Same block:** Attacker calls `claimWithdrawal(withdrawalId)` — passes immediately. Attacker recovers `10_000_000e18` KERNEL.
5. **Same block:** Attacker calls `getReward()`, receiving ~78,537 reward tokens.

Legitimate stakers who held the entire day received only ~9.1% of that day's rewards (~7,862 tokens) instead of 100% (~86,400 tokens) — a direct loss of ~78,537 reward tokens to the attacker.

**Foundry test sketch:**
```solidity
function test_rewardSnipe() public {
    // Setup: existing staker with 1_000_000e18
    vm.prank(existingStaker);
    pool.stake(1_000_000e18);

    // Admin sets maxNumberOfWithdrawalsPerUser (withdrawalDelay stays 0)
    vm.prank(admin);
    pool.setMaxNumberOfWithdrawalsPerUser(10);

    // Attacker frontruns notifyRewardAmount
    vm.prank(attacker);
    pool.stake(10_000_000e18);

    // Admin notifies reward
    vm.prank(admin);
    pool.notifyRewardAmount(604_800e18);

    // Advance 1 day
    vm.warp(block.timestamp + 86_400);

    // Attacker exits
    vm.startPrank(attacker);
    pool.initiateWithdrawal(10_000_000e18);
    pool.claimWithdrawal(1);
    pool.getReward();
    vm.stopPrank();

    // Assert attacker received ~90.9% of 1-day rewards
    assertApproxEqRel(rewardToken.balanceOf(attacker), 78_537e18, 0.01e18);
}
```