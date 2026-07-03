### Title
Precision Loss in `notifyRewardAmount` Due to Division Before Multiplication Permanently Freezes Reward Tokens - (`contracts/KERNEL/KernelDepositPool.sol`)

### Summary

`KernelDepositPool.notifyRewardAmount()` computes `rewardRate` by dividing `receivedAmount` by `duration` using integer division. This truncated `rewardRate` is later multiplied by `timeDelta` and `DECIMAL_PRECISION` in `rewardPerToken()`. Because the division occurs before the multiplication, up to `duration - 1` reward tokens per period are permanently locked in the contract and never distributed to stakers.

### Finding Description

In `notifyRewardAmount()`, the reward rate is set as:

```solidity
rewardRate = receivedAmount / duration;
``` [1](#0-0) 

This truncated `rewardRate` is then used in `rewardPerToken()`:

```solidity
return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / totalKernelStaked;
``` [2](#0-1) 

Expanding the full reward distribution over a complete period:

```
totalDistributed = rewardRate * duration
                 = (receivedAmount / duration) * duration
                 ≤ receivedAmount
```

The difference `receivedAmount % duration` is permanently stuck in the contract. This is the classic division-before-multiplication precision loss: the division at `notifyRewardAmount` time truncates before the multiplication at `rewardPerToken` time can recover the full value.

The `else` branch compounds the issue across periods:

```solidity
uint256 remaining = (finishAt - block.timestamp) * rewardRate;
rewardRate = (receivedAmount + remaining) / duration;
``` [3](#0-2) 

Here `remaining` is itself computed from the already-truncated `rewardRate`, so the precision loss from the previous period is carried forward and compounds with each new reward notification.

### Impact Explanation

Reward tokens equal to `receivedAmount % duration` are permanently frozen in the contract per reward period. For a 30-day duration (`2_592_000` seconds) with a low-decimal reward token (e.g., 6 decimals), up to `2_591_999` base units (~2.59 tokens) are lost per period. Over many periods this accumulates. The tokens are irrecoverable — there is no admin sweep function for excess reward tokens. This matches **Medium: Permanent freezing of unclaimed yield**.

### Likelihood Explanation

This occurs on every call to `notifyRewardAmount()`. Any staker who calls `getReward()` after a full reward period receives slightly less than the deposited reward amount. The entry path is fully permissionless from the staker's perspective: stake KERNEL, wait for the period to end, call `getReward()`, and observe the shortfall. The admin trigger (`notifyRewardAmount`) is a routine operational action, not an attack.

### Recommendation

Scale `rewardRate` by a precision factor to avoid truncation at storage time, then descale when consuming it:

```solidity
uint256 internal constant RATE_PRECISION = 1e18;

// In notifyRewardAmount:
rewardRate = receivedAmount * RATE_PRECISION / duration;

// In rewardPerToken:
return rewardPerTokenStored
    + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / (totalKernelStaked * RATE_PRECISION);
```

Alternatively, track the leftover amount and roll it into the next reward period:

```solidity
uint256 leftover = receivedAmount % duration;
rewardRate = (receivedAmount - leftover) / duration;
// store leftover and add to next notifyRewardAmount call
```

### Proof of Concept

```solidity
// duration = 7 days = 604800 seconds
// receivedAmount = 1_000_000e6 (1M USDC, 6 decimals)
uint256 duration = 604_800;
uint256 receivedAmount = 1_000_000e6;

uint256 rewardRate = receivedAmount / duration;
// rewardRate = 1_653_439

uint256 totalDistributed = rewardRate * duration;
// totalDistributed = 1_653_439 * 604_800 = 999_999_667_200

uint256 permanentlyLost = receivedAmount - totalDistributed;
// permanentlyLost = 1_000_000_000_000 - 999_999_667_200 = 332_800 base units = 0.3328 USDC
// Per period, 0.33 USDC is permanently frozen in the contract.
// Over 52 weekly periods: ~17.3 USDC permanently frozen.
``` [4](#0-3)

### Citations

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
