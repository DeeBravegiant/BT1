### Title
Reward Token Precision Loss Due to Integer Division Truncation Permanently Locks Yield in `KernelDepositPool` - (File: contracts/KERNEL/KernelDepositPool.sol)

---

### Summary

`KernelDepositPool.notifyRewardAmount()` computes `rewardRate = receivedAmount / duration` using integer division. The remainder `receivedAmount % duration` is silently discarded. Because the contract has no token-recovery function, these truncated reward tokens are permanently locked and never distributed to stakers.

---

### Finding Description

In `notifyRewardAmount`, the reward rate is set as:

```solidity
if (block.timestamp >= finishAt) {
    rewardRate = receivedAmount / duration;
} else {
    uint256 remaining = (finishAt - block.timestamp) * rewardRate;
    rewardRate = (receivedAmount + remaining) / duration;
}
``` [1](#0-0) 

Integer division truncates `receivedAmount % duration` wei on every call. These tokens have already been transferred into the contract via `safeTransferFrom` but are never accounted for in `rewardRate`, so they can never be claimed by any staker via `getReward()`. [2](#0-1) 

The `getReward()` function only transfers `rewards[msg.sender]`, which is derived entirely from `rewardRate`. There is no `recoverERC20`, sweep, or admin rescue function anywhere in the contract. [3](#0-2) 

When a reward period is extended (the `else` branch), `remaining = (finishAt - block.timestamp) * rewardRate` is itself computed from the already-truncated `rewardRate`, so the precision loss from prior periods propagates forward and compounds with each successive `notifyRewardAmount` call. [4](#0-3) 

---

### Impact Explanation

Every call to `notifyRewardAmount` permanently locks up to `duration - 1` wei of the reward token. With `MAX_WITHDRAWAL_DELAY = 30 days` and a typical `duration` of 7 days (604 800 seconds), up to 604 799 wei are lost per period. For reward tokens with low decimals (e.g., 6-decimal USDC), this is up to ~0.6 tokens per period. Over many periods the cumulative locked amount grows without bound and is irrecoverable.

Impact: **Permanent freezing of unclaimed yield (High).** [5](#0-4) 

---

### Likelihood Explanation

The precision loss is deterministic and occurs on every single call to `notifyRewardAmount`. No special conditions are required — it is an inherent consequence of integer division in the reward-rate formula. The admin calling this function in normal operation is the only trigger needed. [6](#0-5) 

---

### Recommendation

Track the cumulative reward sum and divide only at distribution time, analogous to the M-25 fix. Concretely, store the raw `receivedAmount` in a `totalRewardBalance` accumulator and compute the effective rate as `totalRewardBalance / duration` only when needed, or add a token-rescue function so the admin can recover the truncated dust:

```solidity
// Option A: store sum, divide on read
rewardRate = receivedAmount / duration;
uint256 dust = receivedAmount % duration;
// transfer dust back to caller or to a treasury

// Option B: add a rescue function
function recoverERC20(address token, uint256 amount) external onlyRole(DEFAULT_ADMIN_ROLE) {
    IERC20(token).safeTransfer(msg.sender, amount);
}
``` [1](#0-0) 

---

### Proof of Concept

**Setup:** `duration = 604800` (7 days), reward token has 6 decimals.

1. Admin calls `notifyRewardAmount` with `_amount = 1_000_000` (1 USDC).
2. Contract receives `receivedAmount = 1_000_000`.
3. `rewardRate = 1_000_000 / 604_800 = 1` (wei/second).
4. `1_000_000 % 604_800 = 395_200` wei (≈ 0.395 USDC) is permanently locked.
5. After the full 7-day period, stakers collectively receive `rewardRate * duration = 604_800` wei, not `1_000_000` wei.
6. The 395 200 wei difference sits in the contract balance forever — `getReward()` can never reach it because it is not reflected in any user's `rewards[account]`.

Repeating over 10 periods locks ≈ 3.95 USDC with no recovery path. [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L32-32)
```text
    uint256 public constant DECIMAL_PRECISION = 1e18;
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L382-390)
```text
    function getReward() external nonReentrant updateReward(msg.sender) {
        uint256 rewardAmount = rewards[msg.sender];

        if (rewardAmount > 0) {
            rewards[msg.sender] = 0;
            rewardsToken.safeTransfer(msg.sender, rewardAmount);
            emit RewardsClaimed(msg.sender, rewardAmount);
        }
    }
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
