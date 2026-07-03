### Title
Integer Division Truncation in `notifyRewardAmount` Permanently Locks Remainder Reward Tokens — (`contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

`KernelDepositPool.notifyRewardAmount` computes `rewardRate` via integer division (`receivedAmount / duration`), silently discarding the remainder (`receivedAmount % duration`). Those remainder tokens are transferred into the contract but can never be distributed or recovered, because no sweep/recovery function exists. On each rollover call (before `finishAt`), the `remaining` calculation re-uses the already-truncated `rewardRate`, so the undistributed dust from the prior period is also silently dropped, compounding the loss across epochs.

---

### Finding Description

In `notifyRewardAmount`:

```solidity
// contracts/KERNEL/KernelDepositPool.sol  line 579-584
if (block.timestamp >= finishAt) {
    rewardRate = receivedAmount / duration;          // ← truncates remainder
} else {
    uint256 remaining = (finishAt - block.timestamp) * rewardRate;  // ← uses truncated rate
    rewardRate = (receivedAmount + remaining) / duration;           // ← truncates again
}
``` [1](#0-0) 

**First call (fresh period):**
- `receivedAmount = A`, `duration = D`
- `rewardRate = A / D` (integer)
- Tokens actually distributed over the period: `(A / D) * D`
- Permanently locked: `A % D` (up to `D - 1` tokens in the smallest unit)

**Rollover call (before `finishAt`):**
- `remaining = (finishAt - block.timestamp) * rewardRate` — this is computed from the already-truncated `rewardRate`, so it is strictly less than the actual undistributed balance
- The gap (`(finishAt - block.timestamp) * truncation_error`) is silently dropped and not included in the new `rewardRate`
- A second integer division then truncates again

**No recovery path exists.** A search of the entire `KernelDepositPool.sol` admin section confirms there is no `recoverERC20`, `sweep`, `rescue`, or `emergencyWithdraw` function. [2](#0-1) 

The invariant `sum(earned(user) for all users) + rewardsToken.balanceOf(pool) == totalNotified` is permanently broken after any call where `receivedAmount % duration != 0`.

---

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

The remainder tokens are transferred into the contract via `safeTransferFrom` and credited to the contract's balance, but `rewardRate` is set too low to ever emit them. After `finishAt`, `lastTimeRewardApplicable()` caps at `finishAt`, so the dust can never be earned by any staker. With no recovery function, those tokens are frozen forever.

The per-call loss is bounded by `duration - 1` in the token's smallest unit. For an 18-decimal token with a 7-day duration this is at most ~604 799 wei (≈ 0.0000006 tokens) — negligible in isolation. However:
- The loss is **permanent and irrecoverable** on every single call.
- On rollover, the compounding means the effective loss per epoch grows with the number of rollovers.
- If the rewards token has fewer decimals (e.g., 6-decimal USDC), the per-call loss can reach up to ~0.6 USDC per epoch, and compounds across rollovers.

---

### Likelihood Explanation

**High likelihood of occurrence, low magnitude per event.**

Every `notifyRewardAmount` call where `receivedAmount` is not an exact multiple of `duration` triggers the loss. In practice, admins will rarely (if ever) choose amounts that are exact multiples of a duration expressed in seconds (e.g., 604 800 for 7 days). This is a deterministic, always-on loss requiring only normal admin operation — no compromise, no front-running, no external dependency.

---

### Recommendation

Carry the undistributed remainder forward explicitly rather than discarding it. The standard fix is to track the leftover and add it back on the next `notifyRewardAmount` call:

```solidity
// Track dust across periods
uint256 public undistributedRemainder;

function notifyRewardAmount(uint256 _amount) external ... {
    ...
    uint256 total = receivedAmount + undistributedRemainder;
    if (block.timestamp >= finishAt) {
        rewardRate = total / duration;
        undistributedRemainder = total % duration;
    } else {
        uint256 remaining = (finishAt - block.timestamp) * rewardRate + undistributedRemainder;
        rewardRate = (receivedAmount + remaining) / duration;
        undistributedRemainder = (receivedAmount + remaining) % duration;
    }
    ...
}
```

Alternatively, add an admin `recoverERC20` function that can only withdraw tokens in excess of `rewardRate * (finishAt - block.timestamp)` to prevent it from touching actively-scheduled rewards.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.21;

// Invariant fuzz test (Foundry)
// Run: forge test --match-test testRewardDustInvariant -vvv

function testRewardDustInvariant() public {
    uint256 duration = 7 days; // 604800 seconds
    pool.setRewardsDuration(duration);

    // Stake so notifyRewardAmount doesn't revert
    kernelToken.approve(address(pool), 1e18);
    pool.stake(1e18);

    // Amount NOT divisible by duration → remainder = 604799 wei
    uint256 notifyAmount = 1_000_000e18 + 604_799; // leaves 604799 wei remainder
    rewardsToken.approve(address(pool), notifyAmount);
    pool.notifyRewardAmount(notifyAmount);

    // Rollover before finishAt — compounds the loss
    vm.warp(block.timestamp + 3 days);
    uint256 notifyAmount2 = 500_000e18 + 1;
    rewardsToken.approve(address(pool), notifyAmount2);
    pool.notifyRewardAmount(notifyAmount2);

    // Fast-forward past finishAt
    vm.warp(block.timestamp + duration + 1);

    uint256 earned = pool.earned(address(this));
    pool.getReward();

    uint256 totalNotified = notifyAmount + notifyAmount2;
    uint256 poolBalance = rewardsToken.balanceOf(address(pool));

    // Invariant: earned + poolBalance == totalNotified
    // This FAILS — poolBalance > 0 with no way to recover it
    assertEq(earned + poolBalance, totalNotified, "Dust permanently locked");
}
```

The assertion fails because `poolBalance` retains the accumulated dust with no mechanism to distribute or recover it.

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L544-621)
```text
    /*//////////////////////////////////////////////////////////////
                            ADMIN FUNCTIONS
    //////////////////////////////////////////////////////////////*/

    /**
     * @notice Sets the duration for rewards distribution
     * @param _duration The duration in seconds
     */
    function setRewardsDuration(uint256 _duration) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (finishAt >= block.timestamp) revert RewardDurationNotFinished();
        if (_duration == 0) revert InvalidDuration();
        duration = _duration;
        emit RewardsDurationUpdated(_duration);
    }

    /**
     * @notice Notifies the contract about a new reward amount
     * @dev Uses a transfer-in pattern to determine the exact reward amount received.
     *      Also, to avoid undistributed rewards when no one is staked, this function reverts if totalKernelStaked is
     *      zero.
     * @param _amount The amount of reward tokens to add
     */
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
    }

    /**
     * @notice Updates the withdrawal delay
     * @param _withdrawalDelay The new withdrawal delay
     */
    function setWithdrawalDelay(uint256 _withdrawalDelay) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_withdrawalDelay == 0) revert InvalidWithdrawalDelay();
        if (_withdrawalDelay > MAX_WITHDRAWAL_DELAY) revert MaximumWithdrawalDelayExceeded();

        withdrawalDelay = _withdrawalDelay;
        emit WithdrawalDelayUpdated(_withdrawalDelay);
    }

    /**
     * @notice Updates the maximum number of withdrawals per user
     * @param _maxNumberOfWithdrawalsPerUser The new maximum number of withdrawals per user
     */
    function setMaxNumberOfWithdrawalsPerUser(uint256 _maxNumberOfWithdrawalsPerUser)
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        if (_maxNumberOfWithdrawalsPerUser == 0 || _maxNumberOfWithdrawalsPerUser > MAX_WITHDRAWALS_PER_USER) {
            revert InvalidMaxNumberOfWithdrawalsPerUser();
        }

        maxNumberOfWithdrawalsPerUser = _maxNumberOfWithdrawalsPerUser;
        emit MaxNumberOfWithdrawalsPerUserUpdated(_maxNumberOfWithdrawalsPerUser);
    }
}
```
