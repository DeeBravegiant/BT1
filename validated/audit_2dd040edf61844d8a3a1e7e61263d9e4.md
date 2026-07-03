### Title
`setRewardsDuration` Practically Always Reverts Because `notifyRewardAmount` Constantly Extends `finishAt` - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.setRewardsDuration` contains a guard that reverts whenever `finishAt >= block.timestamp`. Because `notifyRewardAmount` is expected to be called regularly and always resets `finishAt = block.timestamp + duration`, the guard condition is perpetually true during normal operation, making `setRewardsDuration` permanently uncallable without first halting reward distribution entirely.

### Finding Description
`setRewardsDuration` is the only mechanism for the admin to change the reward distribution window (`duration`):

```solidity
function setRewardsDuration(uint256 _duration) external onlyRole(DEFAULT_ADMIN_ROLE) {
    if (finishAt >= block.timestamp) revert RewardDurationNotFinished();
    ...
    duration = _duration;
}
``` [1](#0-0) 

The revert condition `finishAt >= block.timestamp` is satisfied whenever an active reward period exists. `notifyRewardAmount` — which is the routine admin call to inject new rewards — unconditionally overwrites `finishAt`:

```solidity
finishAt = block.timestamp + duration;
``` [2](#0-1) 

Every call to `notifyRewardAmount` pushes `finishAt` forward by `duration` seconds. Because this function is expected to be called on a recurring basis to keep rewards flowing to stakers, `finishAt` is always in the future during normal protocol operation. The only way to satisfy `finishAt < block.timestamp` is to deliberately stop calling `notifyRewardAmount` and wait for the entire current period to expire — which means halting all reward distribution for that window. [3](#0-2) 

### Impact Explanation
The admin cannot adjust `duration` while rewards are actively being distributed. Any need to shorten or lengthen the reward period (e.g., in response to changed tokenomics, a governance decision, or an emergency) requires first stopping reward distribution and waiting out the full current period. This is a functional failure: the contract does not deliver the administrative capability it promises via `setRewardsDuration`, and stakers receive rewards at a rate/duration that cannot be corrected without a forced reward halt.

**Impact: Low — Contract fails to deliver promised returns (admin cannot update reward duration as designed).**

### Likelihood Explanation
The condition is triggered by normal, expected protocol operation. As long as `notifyRewardAmount` is called at least once per `duration` window (which is the intended usage), `finishAt` will always be in the future and `setRewardsDuration` will always revert. This is not a rare edge case; it is the default operational state of the contract.

### Recommendation
Remove the `finishAt >= block.timestamp` guard from `setRewardsDuration`, mirroring the fix applied in the referenced Berachain commit. Instead, allow the admin to update `duration` at any time. If the concern is mid-period consistency, the new `duration` can simply take effect on the next `notifyRewardAmount` call:

```solidity
function setRewardsDuration(uint256 _duration) external onlyRole(DEFAULT_ADMIN_ROLE) {
    if (_duration == 0) revert InvalidDuration();
    duration = _duration;
    emit RewardsDurationUpdated(_duration);
}
```

### Proof of Concept

1. Admin calls `notifyRewardAmount(X)` → `finishAt` is set to `block.timestamp + duration` (e.g., 7 days from now).
2. Admin immediately calls `setRewardsDuration(newDuration)`.
3. The check `if (finishAt >= block.timestamp)` evaluates to `true` (since `finishAt` is 7 days in the future).
4. Transaction reverts with `RewardDurationNotFinished`.
5. Admin calls `notifyRewardAmount` again the next day → `finishAt` is again pushed 7 days forward.
6. Step 2–5 repeat indefinitely; `setRewardsDuration` is permanently blocked as long as rewards are being distributed. [1](#0-0) [4](#0-3)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L552-557)
```text
    function setRewardsDuration(uint256 _duration) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (finishAt >= block.timestamp) revert RewardDurationNotFinished();
        if (_duration == 0) revert InvalidDuration();
        duration = _duration;
        emit RewardsDurationUpdated(_duration);
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
