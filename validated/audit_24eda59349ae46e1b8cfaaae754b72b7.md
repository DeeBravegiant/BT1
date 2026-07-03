### Title
`rewardRate` Integer Division Truncation Causes Permanent Loss of Reward Tokens - (`contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

`KernelDepositPool.notifyRewardAmount()` computes `rewardRate` via raw integer division `receivedAmount / duration` with no fixed-point scaling. The remainder `receivedAmount % duration` is permanently stranded in the contract and never distributed to stakers. For low-decimal reward tokens (e.g., USDC at 6 decimals) or large durations, the truncation loss per epoch can be substantial.

---

### Finding Description

In `notifyRewardAmount()`, the per-second reward rate is set as:

```solidity
// contracts/KERNEL/KernelDepositPool.sol lines 579-584
if (block.timestamp >= finishAt) {
    rewardRate = receivedAmount / duration;
} else {
    uint256 remaining = (finishAt - block.timestamp) * rewardRate;
    rewardRate = (receivedAmount + remaining) / duration;
}
``` [1](#0-0) 

`rewardRate` is stored as raw token units per second — no scaling multiplier is applied before the division. Solidity integer division silently discards the fractional part. The discarded amount is `receivedAmount % duration` tokens, which remain in the contract's balance but are never accounted for in any future distribution.

The downstream accumulator in `rewardPerToken()` does multiply by `DECIMAL_PRECISION` (1e18), but this only preserves precision in the per-staker share math — it does not recover the tokens already lost to truncation in `rewardRate`:

```solidity
// contracts/KERNEL/KernelDepositPool.sol lines 412-413
return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / totalKernelStaked;
``` [2](#0-1) 

The `rewardsToken` is a configurable ERC20:

```solidity
// contracts/KERNEL/KernelDepositPool.sol line 66
IERC20 public rewardsToken;
``` [3](#0-2) 

---

### Impact Explanation

Every call to `notifyRewardAmount()` permanently locks `receivedAmount % duration` reward tokens in the contract. These tokens are not redistributable — there is no sweep or recovery function. Stakers collectively receive less yield than was deposited.

**Concrete example with USDC (6 decimals) as `rewardsToken`:**

| Parameter | Value |
|---|---|
| `receivedAmount` | `1_000_000` (1 USDC) |
| `duration` | `604_800` (7 days) |
| `rewardRate` | `1_000_000 / 604_800 = 1` (truncated from 1.653) |
| Tokens actually distributed | `1 × 604_800 = 604_800` (0.6048 USDC) |
| Tokens permanently lost | `395_200` (~39.5% of the deposit) |

For 18-decimal tokens the loss is at most `duration − 1` wei per epoch (negligible). For 6-decimal tokens with a 7-day duration the loss can exceed 39% of each reward epoch. The loss compounds across every `notifyRewardAmount` call.

**Impact classification:** Medium — Permanent freezing of unclaimed yield.

---

### Likelihood Explanation

- `rewardsToken` is set at initialization and can be any ERC20, including low-decimal tokens.
- `duration` is admin-configurable up to 30 days (`MAX_WITHDRAWAL_DELAY`), amplifying the truncation.
- The truncation occurs unconditionally on every `notifyRewardAmount` call — no special conditions required.
- Any staker calling `getReward()` will receive a proportionally reduced amount with no recourse. [4](#0-3) 

---

### Recommendation

Scale `rewardRate` by a precision multiplier (e.g., `1e18`) before storing it, and divide by the same multiplier when computing earned amounts. This is the standard Synthetix pattern fix:

```solidity
// Store with scaling
rewardRate = (receivedAmount * DECIMAL_PRECISION) / duration;

// Accumulate (remove the extra DECIMAL_PRECISION multiply here)
return rewardPerTokenStored + (rewardRate * elapsed) / totalKernelStaked;

// Earned (no change needed)
return (balanceOf[_account] * (rewardPerToken() - userRewardPerTokenPaid[_account]) / DECIMAL_PRECISION) + rewards[_account];
```

Alternatively, track the undistributed dust and roll it into the next epoch's `notifyRewardAmount` call.

---

### Proof of Concept

1. Admin deploys `KernelDepositPool` with `rewardsToken = USDC` (6 decimals) and calls `setRewardsDuration(604_800)` (7 days).
2. Admin calls `notifyRewardAmount(1_000_000)` (1 USDC).
3. Contract computes: `rewardRate = 1_000_000 / 604_800 = 1`.
4. After 7 days, total distributed = `1 × 604_800 = 604_800` units = 0.6048 USDC.
5. Remaining `395_200` units (0.3952 USDC, ~39.5%) sit in the contract balance permanently — no staker can ever claim them, and no admin function can recover them.
6. Any staker calling `getReward()` receives only 60.5% of the intended yield. [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L66-67)
```text
    IERC20 public rewardsToken;

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L552-556)
```text
    function setRewardsDuration(uint256 _duration) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (finishAt >= block.timestamp) revert RewardDurationNotFinished();
        if (_duration == 0) revert InvalidDuration();
        duration = _duration;
        emit RewardsDurationUpdated(_duration);
```

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
