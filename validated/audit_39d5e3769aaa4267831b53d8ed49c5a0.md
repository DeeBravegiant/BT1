Audit Report

## Title
Uninitialized `withdrawalDelay` Enables Immediate Withdrawal After Staking, Allowing Yield Theft - (File: `contracts/KERNEL/KernelDepositPool.sol`)

## Summary
`KernelDepositPool` declares `withdrawalDelay` as a plain `uint256` state variable that defaults to `0` and is never assigned in `initialize()`. Because `notifyRewardAmount()` imposes no requirement that `withdrawalDelay > 0` before activating a reward period, any unprivileged user can stake a large amount of KERNEL, wait one or more blocks for rewards to accrue, claim those rewards via `getReward()`, and immediately recover their principal via `initiateWithdrawal()` + `claimWithdrawal()` in the same block — stealing a disproportionate share of yield from legitimate long-term stakers.

## Finding Description
`withdrawalDelay` is declared at L96 with no initializer:
```solidity
uint256 public withdrawalDelay;
```
The `initialize()` function (L259–271) sets `kernelToken`, `rewardsToken`, and access-control roles, but never assigns `withdrawalDelay`, leaving it at the EVM default of `0`.

`notifyRewardAmount()` (L566–592) only guards against `totalKernelStaked == 0`; it does not check `withdrawalDelay > 0`. Once called by the admin, a live reward period begins with `rewardRate > 0` and `finishAt = block.timestamp + duration`.

In `initiateWithdrawal()` (L330):
```solidity
uint256 unlockTime = block.timestamp + withdrawalDelay;
```
With `withdrawalDelay == 0`, `unlockTime == block.timestamp`.

In `claimWithdrawal()` (L355–357):
```solidity
if (block.timestamp < withdrawal.unlockTime) revert WithdrawalNotReady();
```
Since `block.timestamp >= block.timestamp` is always true, the withdrawal is claimable in the same block it was initiated.

`setWithdrawalDelay()` (L598–604) explicitly rejects `0`, so the initial unset state cannot be restored once corrected — but there is no enforcement that it must be set before rewards begin.

Note: `maxNumberOfWithdrawalsPerUser` is also uninitialized (defaults to `0`), which would cause `initiateWithdrawal()` to revert at L323 (`0 >= 0`). However, `maxNumberOfWithdrawalsPerUser` must be set to a non-zero value via `setMaxNumberOfWithdrawalsPerUser()` for the contract to be functional at all (no user could ever withdraw otherwise). The realistic deployment sequence therefore includes setting `maxNumberOfWithdrawalsPerUser` while potentially omitting `setWithdrawalDelay()`, leaving the attack window open.

**Exploit path:**
1. Admin deploys, sets `maxNumberOfWithdrawalsPerUser`, and calls `notifyRewardAmount()` without first calling `setWithdrawalDelay()`.
2. Attacker observes `withdrawalDelay == 0` and `rewardRate > 0` on-chain.
3. Attacker calls `stake(largeAmount)` — `updateReward` snapshots current `rewardPerTokenStored`.
4. One or more blocks pass; `rewardPerToken()` increases proportionally to attacker's dominant share of `totalKernelStaked`.
5. Attacker calls `getReward()` — claims the majority of rewards for that window.
6. Attacker calls `initiateWithdrawal(largeAmount)` — `unlockTime = block.timestamp`.
7. Attacker calls `claimWithdrawal(id)` in the same block — recovers full principal immediately.

Legitimate stakers receive near-zero rewards for the same period because the attacker held a dominant share of `totalKernelStaked`.

## Impact Explanation
**High — Theft of unclaimed yield.** The attacker extracts reward tokens that were accrued during the window in which they held a dominant stake position, directly reducing the yield received by all other stakers for that period. The attacker recovers their full principal with no lock-up cost, making the attack risk-free and repeatable across multiple reward periods as long as `withdrawalDelay` remains `0`.

## Likelihood Explanation
**Medium.** The deployment sequence where `notifyRewardAmount()` is called before `setWithdrawalDelay()` is realistic — the contract itself documents the `totalKernelStaked > 0` precondition for `notifyRewardAmount()` but places no analogous requirement on `withdrawalDelay`. The state `withdrawalDelay == 0` with an active reward period is fully observable on-chain by any unprivileged actor, and the exploit requires only standard ERC-20 token approval and public contract calls. It is repeatable for every reward period where the delay remains unset.

## Recommendation
1. **Initialize `withdrawalDelay` to a safe non-zero value** (e.g., `7 days`) inside `initialize()`:
   ```solidity
   withdrawalDelay = 7 days;
   ```
2. **Add a guard in `notifyRewardAmount()`** that reverts if `withdrawalDelay == 0`:
   ```solidity
   if (withdrawalDelay == 0) revert InvalidWithdrawalDelay();
   ```
3. Similarly, consider initializing `maxNumberOfWithdrawalsPerUser` to a safe default in `initialize()` to avoid a parallel uninitialized-state issue.

## Proof of Concept
```solidity
// Preconditions:
//   - withdrawalDelay == 0 (never initialized)
//   - maxNumberOfWithdrawalsPerUser > 0 (set by admin, required for contract to function)
//   - notifyRewardAmount() called by admin (reward period active, rewardRate > 0)
//   - Attacker holds sufficient KERNEL tokens and has approved the contract

// 1. Attacker stakes large amount, capturing dominant share of totalKernelStaked
kernelDepositPool.stake(1_000_000e18);

// 2. Advance 1 block — rewards accrue for ~12 seconds at attacker's dominant share
vm.roll(block.number + 1);
vm.warp(block.timestamp + 12);

// 3. Claim rewards — attacker captures majority of distributed yield
kernelDepositPool.getReward();

// 4. Initiate withdrawal — unlockTime = block.timestamp (delay == 0)
kernelDepositPool.initiateWithdrawal(1_000_000e18);

// 5. Claim withdrawal in same block — full principal returned immediately
kernelDepositPool.claimWithdrawal(1);

// Result: attacker recovers full 1_000_000e18 KERNEL principal
//         plus disproportionate share of reward tokens
//         Legitimate stakers earned near-zero rewards for the same 12-second window
```