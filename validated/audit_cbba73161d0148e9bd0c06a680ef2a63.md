### Title
Rewards Permanently Locked When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary

`KernelDepositPool` implements an SNX-style reward distribution. When `totalKernelStaked` reaches zero during an active reward window, `rewardPerToken()` freezes `rewardPerTokenStored` while the `updateReward` modifier still advances `updatedAt` to the current time. The rewards that were scheduled to be emitted during the zero-staking interval are never distributed and are permanently locked in the contract, with no recovery path.

### Finding Description

`rewardPerToken()` short-circuits when `totalKernelStaked == 0`:

```solidity
// KernelDepositPool.sol L408-413
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;   // rewards stop accruing
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

The `updateReward` modifier, however, unconditionally advances `updatedAt`:

```solidity
// KernelDepositPool.sol L232-241
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    updatedAt = lastTimeRewardApplicable();   // time advances even when totalKernelStaked == 0
    ...
}
```

`initiateWithdrawal()` immediately decrements `totalKernelStaked` at line 326:

```solidity
balanceOf[msg.sender] -= _amount;
totalKernelStaked -= _amount;   // L326 — instant effect, no delay
```

**Attack / loss path:**

1. Admin calls `notifyRewardAmount(X)` at `t=0`; `finishAt = t + duration`, `rewardRate = X / duration`.
2. All stakers call `initiateWithdrawal()`. Each call triggers `updateReward`, which advances `updatedAt` to the current time. After the last withdrawal `totalKernelStaked == 0`.
3. From `t=T1` (last withdrawal) to `t=T2` (next stake or `notifyRewardAmount`), `rewardPerToken()` returns `rewardPerTokenStored` unchanged, but `updatedAt` is already at `T1`.
4. When a new staker stakes at `T2`, `updateReward` runs: `rewardPerToken()` still returns `rewardPerTokenStored` (since `totalKernelStaked` is still 0 at the start of the call), and `updatedAt` is set to `T2`.
5. When `notifyRewardAmount(Y)` is called at `T2` (with `T2 < finishAt`):
   - `remaining = (finishAt - T2) * rewardRate` — this only covers `T2 → finishAt`.
   - The rewards for `T1 → T2` (`= rewardRate * (T2 - T1)`) are **not included** in `remaining` and are silently discarded.
   - `rewardRate = (Y + remaining) / duration` — the lost slice is gone.
6. If `T2 >= finishAt`, the situation is worse: `rewardRate = Y / duration` with zero rollover, and the entire undistributed balance from the previous period is locked.

There is no `recoverERC20` or equivalent rescue function in the contract, so the locked reward tokens are irrecoverable.

The contract's own NatSpec acknowledges the risk but relies on an off-chain operational assumption ("ensuring there are always some tokens staked… for the entire duration of the reward period") that cannot be enforced on-chain.

### Impact Explanation

Reward tokens that were scheduled for distribution during the zero-staking interval are permanently locked in the contract. Stakers who re-enter after the gap receive no compensation for the missed period, and the admin cannot recover or re-queue the lost tokens. This constitutes **permanent freezing of unclaimed yield**.

### Likelihood Explanation

Any staker can call `initiateWithdrawal()` at any time — no permission required. A coordinated or even uncoordinated mass exit during a reward window (e.g., in response to a market event) is a realistic scenario. The `withdrawalDelay` does not prevent `totalKernelStaked` from hitting zero immediately upon `initiateWithdrawal()`, since the decrement happens at the moment of initiation, not at claim time.

### Recommendation

Track the cumulative rewards that were "skipped" while `totalKernelStaked == 0` and roll them into the next `notifyRewardAmount` call, similar to the fix applied to `CLPool.timeNoStakedLiquidity`. Concretely:

- Introduce a `unallocatedRewards` accumulator.
- In `rewardPerToken()` (or a dedicated hook), when `totalKernelStaked == 0`, accumulate `rewardRate * timeDelta` into `unallocatedRewards` instead of silently discarding it.
- In `notifyRewardAmount`, add `unallocatedRewards` to `receivedAmount` before computing the new `rewardRate`, then reset `unallocatedRewards = 0`.

Alternatively, add an admin-callable `recoverUnallocatedRewards()` that re-queues any reward token balance in excess of what is owed to current stakers.

### Proof of Concept

```
t=0:   notifyRewardAmount(1_000e18)
       rewardRate = 1_000e18 / duration
       finishAt   = block.timestamp + duration

t=T1:  All stakers call initiateWithdrawal()
       → totalKernelStaked = 0
       → updatedAt = T1  (set by updateReward inside initiateWithdrawal)

       [T1 .. T2]: rewardPerToken() returns rewardPerTokenStored unchanged.
                   Rewards worth rewardRate*(T2-T1) are never distributed.

t=T2:  New staker stakes 1 wei
       → updateReward: rewardPerToken() = rewardPerTokenStored (totalKernelStaked still 0 before +=)
       → updatedAt = T2
       → totalKernelStaked = 1

t=T2:  Admin calls notifyRewardAmount(500e18)
       updateReward(address(0)):
         rewardPerToken() = rewardPerTokenStored + rewardRate*(T2-T2)/1 = rewardPerTokenStored
         updatedAt = T2
       remaining = (finishAt - T2) * rewardRate   // covers only T2→finishAt
       rewardRate = (500e18 + remaining) / duration

       Lost: rewardRate * (T2 - T1) tokens — permanently locked in contract.
             No recoverERC20 exists. Funds are irrecoverable.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L14-22)
```text
/**
 * @title Kernel Staking Rewards Contract
 * @dev Implements a basic staking mechanism with rewards
 * @dev Modified from https://github.com/Synthetixio/synthetix/blob/develop/contracts/StakingRewards.sol
 * @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
 *      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
 *      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
 *      as well as for the entire duration of the reward period. Otherwise, staking just 1 wei by any address
 *      will ensure that the contract never has any unallocated rewards.
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L232-241)
```text
    modifier updateReward(address _account) {
        rewardPerTokenStored = rewardPerToken();
        updatedAt = lastTimeRewardApplicable();

        if (_account != address(0)) {
            rewards[_account] = earned(_account);
            userRewardPerTokenPaid[_account] = rewardPerTokenStored;
        }

        _;
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L408-414)
```text
    function rewardPerToken() public view returns (uint256) {
        if (totalKernelStaked == 0) {
            return rewardPerTokenStored;
        }
        return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
            / totalKernelStaked;
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L566-591)
```text
    function notifyRewardAmount(uint256 _amount) external onlyRole(DEFAULT_ADMIN_ROLE) updateReward(address(0)) {
        if (_amount == 0) revert AmountZero();

        // Prevent starting a reward period when no tokens are staked to avoid unallocated rewards
        if (totalKernelStaked == 0) revert NoStakedTokens();

        // Transfer reward tokens into the contract
        uint256 balanceBefore = rewardsToken.balanceOf(address(this));
        rewardsToken.safeTransferFrom(msg.sender, address(this), _amount);
        uint256 balanceAfter = rewardsToken.balanceOf(address(this));
        // Calculate the actual amount of tokens received in case of a transfer fee (tax)
        uint256 receivedAmount = balanceAfter - balanceBefore;

        if (block.timestamp >= finishAt) {
            rewardRate = receivedAmount / duration;
        } else {
            uint256 remaining = (finishAt - block.timestamp) * rewardRate;
            rewardRate = (receivedAmount + remaining) / duration;
        }

        if (rewardRate == 0) revert RewardRateZero();

        finishAt = block.timestamp + duration;
        updatedAt = block.timestamp;

        emit NotifyRewardAmount(receivedAmount, finishAt);
```
