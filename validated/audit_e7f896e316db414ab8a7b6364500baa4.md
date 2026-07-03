### Title
Permanent Reward Token Loss Due to Integer Division Truncation in `notifyRewardAmount` - (File: `contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

In `KernelDepositPool.notifyRewardAmount()`, the `rewardRate` is computed via integer division `receivedAmount / duration`. The remainder `receivedAmount % duration` is permanently locked in the contract with no recovery path, meaning stakers never receive the full reward amount deposited by the admin.

---

### Finding Description

`notifyRewardAmount` computes the per-second reward rate as:

```solidity
rewardRate = receivedAmount / duration;
``` [1](#0-0) 

Solidity integer division truncates the result. The total tokens that will actually be distributed over the full period is:

```
rewardRate * duration = receivedAmount - (receivedAmount % duration)
```

The remainder `receivedAmount % duration` (up to `duration - 1` wei) is transferred into the contract but never emitted to any staker. There is no `recoverToken`, `sweep`, or `emergencyWithdraw` function in `KernelDepositPool`, so these tokens are permanently locked.

The same truncation applies in the mid-period top-up branch:

```solidity
uint256 remaining = (finishAt - block.timestamp) * rewardRate;
rewardRate = (receivedAmount + remaining) / duration;
``` [2](#0-1) 

Here `(receivedAmount + remaining) % duration` tokens are permanently locked per top-up call.

The contract itself acknowledges a related dust-locking risk in its NatSpec but only addresses the `totalKernelStaked == 0` scenario, not the integer division truncation: [3](#0-2) 

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but does not lose user principal.**

Every `notifyRewardAmount` call permanently locks up to `duration - 1` wei of reward tokens. With `duration = 7 days = 604,800 seconds`, the maximum loss per call is `604,799` wei. For an 18-decimal reward token this is negligible dust per call, but it accumulates monotonically across every reward period and is irrecoverable with no admin sweep function present.

---

### Likelihood Explanation

**High.** The truncation occurs on every single invocation of `notifyRewardAmount`. It is a structural property of the integer division and is triggered unconditionally by the admin's normal operational flow of funding reward periods. [4](#0-3) 

---

### Recommendation

Track the undistributed remainder and roll it forward into the next reward period, or add an admin-only token recovery function for the `rewardsToken`. The standard fix is:

```solidity
// After computing rewardRate, carry the dust forward:
uint256 distributed = rewardRate * duration;
uint256 dust = receivedAmount - distributed;
// dust is added to the next notifyRewardAmount call's receivedAmount
```

Alternatively, accumulate the dust in a state variable and add it to `receivedAmount` on the next call.

---

### Proof of Concept

**Scenario (illustrative with small numbers):**

- `duration = 604,800` (7 days in seconds)
- Admin calls `notifyRewardAmount` with `receivedAmount = 1,000,000` (reward token units)
- `rewardRate = 1,000,000 / 604,800 = 1` (truncated)
- Total distributed over period: `1 * 604,800 = 604,800`
- Permanently locked: `1,000,000 - 604,800 = 395,200` tokens (~39.5% lost)

**Realistic scenario (18-decimal token, 1M tokens):**

- `receivedAmount = 1_000_000e18`
- `rewardRate = 1_000_000e18 / 604_800 = 1_653_439_153_439_153`
- Total distributed: `1_653_439_153_439_153 * 604_800 = 999_999_999_999_999_974_400`
- Permanently locked: `25,600` wei per call

The loss is dust per call for 18-decimal tokens, but is irrecoverable and accumulates across every reward epoch with no admin recovery path available in the contract. [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L18-22)
```text
 * @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
 *      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
 *      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
 *      as well as for the entire duration of the reward period. Otherwise, staking just 1 wei by any address
 *      will ensure that the contract never has any unallocated rewards.
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
