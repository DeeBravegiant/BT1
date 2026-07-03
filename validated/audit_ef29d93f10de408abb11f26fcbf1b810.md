Audit Report

## Title
Reward Theft via Minimal Stake When `totalKernelStaked` Drops to Zero - (File: `contracts/KERNEL/KernelDepositPool.sol`)

## Summary

The `updateReward` modifier unconditionally advances `updatedAt` to `lastTimeRewardApplicable()` even when `totalKernelStaked == 0`, silently erasing any time elapsed during the zero-stake window. An attacker who stakes 1 wei immediately after `totalKernelStaked` reaches zero captures the entire remaining reward stream for the active period, while rewards that accrued during the zero-stake gap are permanently locked in the contract.

## Finding Description

**Root cause — `updateReward` modifier** [1](#0-0) 

Every function decorated with `updateReward` executes:
```solidity
rewardPerTokenStored = rewardPerToken();   // frozen when totalKernelStaked == 0
updatedAt = lastTimeRewardApplicable();    // always advances to now
```

**`rewardPerToken()` short-circuits on zero stake** [2](#0-1) 

When `totalKernelStaked == 0`, `rewardPerTokenStored` is returned unchanged, but `updatedAt` is still moved forward. The time gap is permanently lost.

**`initiateWithdrawal` reduces `totalKernelStaked` immediately** [3](#0-2) 

There is no floor preventing `totalKernelStaked` from reaching zero mid-period.

**The only guard is in `notifyRewardAmount`** [4](#0-3) 

This check only prevents *starting* a new reward period with zero stake; it does nothing to prevent all stakers from exiting during an active period.

**Exploit flow:**

1. Active reward period: `rewardRate = 1000 KERNEL/day`, `finishAt = T0 + 30 days`.
2. At `T0 + 10 days`, the last staker calls `initiateWithdrawal`. `updateReward` snapshots `rewardPerTokenStored` and `updatedAt = T0 + 10 days`. `totalKernelStaked = 0`.
3. 10 days pass with no interaction. `rewardPerTokenStored` and `updatedAt` remain frozen.
4. At `T0 + 20 days`, attacker calls `stake(1)`. `updateReward` fires:
   - `rewardPerToken()` returns the frozen value (no change to `rewardPerTokenStored`).
   - `updatedAt` jumps to `T0 + 20 days`, erasing the 10-day gap.
   - `userRewardPerTokenPaid[attacker] = rewardPerTokenStored` (frozen value).
   - `balanceOf[attacker] = 1`, `totalKernelStaked = 1`.
5. For the remaining 10 days, `rewardPerToken()` increases by `rewardRate × Δt × 1e18 / 1` each second.
6. At `T0 + 30 days`, attacker calls `getReward()` and receives `rewardRate × 10 days = 10,000 KERNEL` for a 1-wei stake. The 10,000 KERNEL from days 10–20 are permanently locked.

## Impact Explanation

**High — Theft of unclaimed yield.** An unprivileged attacker stakes 1 wei and receives the entire remaining reward stream. In the PoC, 10,000 KERNEL is stolen for a cost of 1 wei. The 10,000 KERNEL from the zero-stake window is additionally frozen forever. Both outcomes are direct, concrete, and on-chain.

## Likelihood Explanation

The trigger condition — `totalKernelStaked` reaching zero during an active reward period — is realistic. KERNEL stakers may exit en masse during market downturns or in response to better yield opportunities. `initiateWithdrawal` is fully permissionless and reduces `totalKernelStaked` immediately upon initiation. [5](#0-4)  The protocol's only mitigation is an off-chain operational promise documented in the NatSpec comment, with no on-chain enforcement. [6](#0-5)  An attacker monitoring the contract can detect when `totalKernelStaked` approaches zero and front-run any re-staking with a 1-wei deposit.

## Recommendation

1. **Prevent zero-stake mid-period**: In `initiateWithdrawal`, revert if the withdrawal would reduce `totalKernelStaked` to zero while `block.timestamp < finishAt`.
2. **Redirect orphaned rewards**: When `totalKernelStaked == 0` and time elapses, track the orphaned reward amount and either extend the reward period or transfer it to the treasury, rather than leaving it claimable by the next staker.
3. **Minimum stake threshold**: Require a meaningful minimum stake so that capturing the full reward stream requires a proportionally significant deposit.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Foundry test outline
// 1. Deploy KernelDepositPool with rewardRate = 1000e18 KERNEL/day, duration = 30 days
// 2. Alice stakes 1000e18 KERNEL at T0; admin calls notifyRewardAmount
// 3. vm.warp(T0 + 10 days); Alice calls initiateWithdrawal(1000e18)
//    → totalKernelStaked == 0, updatedAt == T0 + 10 days
// 4. vm.warp(T0 + 20 days); Attacker calls stake(1)
//    → updateReward: rewardPerTokenStored unchanged, updatedAt jumps to T0+20 days
// 5. vm.warp(T0 + 30 days); Attacker calls getReward()
//    → assertEq(rewardsToken.balanceOf(attacker), 1000e18 * 10 days)  // 10,000 KERNEL
//    → 10,000 KERNEL from days 10–20 permanently locked in contract
```

The `earned()` formula confirms the result: [7](#0-6) 

```
earned(attacker) = 1 * (rewardPerTokenStored_final - rewardPerTokenStored_frozen) / 1e18
                 = 1 * (rewardRate * 10 days * 1e18 / 1) / 1e18
                 = 10,000 KERNEL
```

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L17-23)
```text
 * @dev Modified from https://github.com/Synthetixio/synthetix/blob/develop/contracts/StakingRewards.sol
 * @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
 *      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
 *      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
 *      as well as for the entire duration of the reward period. Otherwise, staking just 1 wei by any address
 *      will ensure that the contract never has any unallocated rewards.
 */
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L232-242)
```text
    modifier updateReward(address _account) {
        rewardPerTokenStored = rewardPerToken();
        updatedAt = lastTimeRewardApplicable();

        if (_account != address(0)) {
            rewards[_account] = earned(_account);
            userRewardPerTokenPaid[_account] = rewardPerTokenStored;
        }

        _;
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-326)
```text
    function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
        if (_amount == 0) revert AmountZero();
        if (balanceOf[msg.sender] < _amount) revert InsufficientStakedBalance();
        if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();

        balanceOf[msg.sender] -= _amount;
        totalKernelStaked -= _amount;
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L408-413)
```text
    function rewardPerToken() public view returns (uint256) {
        if (totalKernelStaked == 0) {
            return rewardPerTokenStored;
        }
        return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
            / totalKernelStaked;
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L421-423)
```text
    function earned(address _account) public view returns (uint256) {
        return (balanceOf[_account] * (rewardPerToken() - userRewardPerTokenPaid[_account]) / DECIMAL_PRECISION)
            + rewards[_account];
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L569-570)
```text
        // Prevent starting a reward period when no tokens are staked to avoid unallocated rewards
        if (totalKernelStaked == 0) revert NoStakedTokens();
```
