### Title
Reward Token Dust Permanently Locked Due to Integer Division Truncation in `notifyRewardAmount()` - (File: `contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

In `KernelDepositPool.notifyRewardAmount()`, the `rewardRate` is computed via integer division (`receivedAmount / duration`). The remainder (`receivedAmount % duration`) is silently discarded and permanently locked in the contract, never distributed to stakers.

---

### Finding Description

`notifyRewardAmount()` computes the per-second reward rate as:

```solidity
if (block.timestamp >= finishAt) {
    rewardRate = receivedAmount / duration;
} else {
    uint256 remaining = (finishAt - block.timestamp) * rewardRate;
    rewardRate = (receivedAmount + remaining) / duration;
}
```

Solidity integer division truncates toward zero. The total rewards actually distributed over the full period is `rewardRate * duration`, which equals `receivedAmount - (receivedAmount % duration)`. The remainder `receivedAmount % duration` is never accounted for in any state variable and has no recovery path in the contract. This dust accumulates with every call to `notifyRewardAmount()`.

The `else` branch compounds the issue: `remaining = (finishAt - block.timestamp) * rewardRate` already carries truncation error from the previous period's `rewardRate`, and the new division again discards a remainder. [1](#0-0) 

---

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

Every invocation of `notifyRewardAmount()` permanently locks up to `duration - 1` wei of the reward token in the contract. Over multiple reward periods this accumulates. There is no `rescueTokens` or sweep function visible in the contract, so the dust is irrecoverable. Stakers collectively lose this yield with no mechanism to claim it. [2](#0-1) 

---

### Likelihood Explanation

**High.** This triggers on every single call to `notifyRewardAmount()` without any special precondition. Any reward amount that is not an exact multiple of `duration` (which is virtually always the case with real token amounts) will produce a non-zero remainder. The admin calling this function is the normal operational path, not an attack.

---

### Recommendation

Track and roll over the undistributed remainder into the next period. A standard fix is:

```solidity
if (block.timestamp >= finishAt) {
    rewardRate = receivedAmount / duration;
} else {
    uint256 remaining = (finishAt - block.timestamp) * rewardRate;
    rewardRate = (receivedAmount + remaining) / duration;
}
// Any dust (receivedAmount % duration or (receivedAmount + remaining) % duration)
// is implicitly carried as contract balance and rolled into the next notifyRewardAmount call,
// OR track it explicitly and add it to the next receivedAmount.
```

Alternatively, use a higher-precision fixed-point accumulator (e.g., store `rewardRate` scaled by `1e18`) so the truncation error is negligible.

---

### Proof of Concept

1. Admin calls `setRewardsDuration(7 days)` → `duration = 604800`.
2. Admin calls `notifyRewardAmount(1_000_000)` with `receivedAmount = 1_000_000`.
   - `rewardRate = 1_000_000 / 604_800 = 1` (truncated).
   - Total distributed over period: `1 * 604_800 = 604_800` tokens.
   - **Permanently locked: `1_000_000 - 604_800 = 395_200` tokens** (≈39.5% of rewards).
3. After `finishAt`, stakers call `getReward()` — they collectively receive only `604_800` tokens. The remaining `395_200` sit in the contract with no claim path. [3](#0-2)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L566-592)
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
    }
```
