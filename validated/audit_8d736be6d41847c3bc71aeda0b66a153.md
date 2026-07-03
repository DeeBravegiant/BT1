### Title
Reward Token Dust Permanently Locked Due to Integer Division Truncation in `notifyRewardAmount` - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.notifyRewardAmount` computes `rewardRate` via integer division (`receivedAmount / duration`), permanently locking the remainder (`receivedAmount % duration`) in the contract with no recovery path. This is the structural analog to the DCT bug: a time-based reward accumulator whose cap/rate calculation silently discards a non-zero remainder, causing stakers to permanently lose yield they are owed.

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

Solidity integer division truncates toward zero. The quantity `receivedAmount % duration` (or `(receivedAmount + remaining) % duration` in the mid-period branch) is never distributed: it sits in the contract's `rewardsToken` balance permanently. There is no `sweep`, `recoverERC20`, or any other admin function in the contract that can retrieve these stranded tokens. [2](#0-1) 

The `rewardPerToken` accumulator only ever credits `rewardRate * elapsedTime / totalKernelStaked` to stakers:

```solidity
return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / totalKernelStaked;
``` [3](#0-2) 

Because `rewardRate` is already truncated, the dust `receivedAmount % duration` is never reachable by any staker through `getReward`. [4](#0-3) 

### Impact Explanation
Every call to `notifyRewardAmount` permanently locks up to `duration - 1` reward-token units in the contract. With no recovery function present, these tokens are irrecoverable. The loss compounds across every reward epoch. For a 30-day duration (`duration = 2_592_000 s`), up to 2,591,999 smallest-unit tokens are lost per call. Depending on the reward token's decimals, this can be material (e.g., a 6-decimal token loses up to ~$2.59 per epoch; a lower-decimal token loses proportionally more). Stakers receive strictly less yield than the protocol intends to distribute.

**Impact: Medium — Permanent freezing of unclaimed yield.**

### Likelihood Explanation
This truncation occurs unconditionally on every `notifyRewardAmount` call, which is a routine admin operation expected to happen at the start of each reward period. No special conditions, attacker action, or protocol state is required. The loss is guaranteed and accumulates monotonically.

**Likelihood: High.**

### Recommendation
Compute the distributable amount as `rewardRate * duration` and return the remainder to the caller, or carry it forward into the next period:

```solidity
// Option A: carry dust forward
uint256 distributable = rewardRate * duration;
uint256 dust = receivedAmount - distributable;
// store dust and add it to the next notifyRewardAmount call

// Option B: return dust to caller
uint256 dust = receivedAmount % duration;
if (dust > 0) rewardsToken.safeTransfer(msg.sender, dust);
```

Alternatively, require that `receivedAmount` is an exact multiple of `duration` before accepting the transfer.

### Proof of Concept
1. `duration = 2_592_000` (30 days).
2. Admin calls `notifyRewardAmount(10_000_000)` (e.g., 10 USDC with 6 decimals).
3. `rewardRate = 10_000_000 / 2_592_000 = 3` (truncated from 3.858…).
4. Total distributed over the period: `3 * 2_592_000 = 7_776_000` units.
5. Permanently locked: `10_000_000 - 7_776_000 = 2_224_000` units ≈ **$2.22 per epoch**, irrecoverable.
6. After 100 epochs: **$222 permanently locked**, with no admin function able to retrieve it. [5](#0-4)

### Citations

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
