### Title
Rewards Permanently Locked in `KernelDepositPool` When `totalKernelStaked` Drops to Zero During Active Reward Period — (`contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

`KernelDepositPool` is a Synthetix-style staking contract where stakers earn `rewardsToken` proportional to their share of `totalKernelStaked`. When `totalKernelStaked` reaches zero during an active reward distribution window, the `rewardPerToken()` accumulator freezes and all remaining rewards for that period become permanently unrecoverable. There is no admin rescue function, no `recoverERC20`, and no mechanism to redirect or reclaim these locked tokens.

---

### Finding Description

The `rewardPerToken()` function short-circuits when `totalKernelStaked == 0`: [1](#0-0) 

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;  // accumulator frozen
    }
    ...
}
```

When the accumulator is frozen, `rewardRate * elapsed_time` worth of reward tokens are silently skipped — they remain in the contract balance but are never credited to any user. The `notifyRewardAmount` function guards against starting a new period with zero stakers: [2](#0-1) 

But this guard only applies at the moment of calling `notifyRewardAmount`. It does **not** prevent all stakers from withdrawing mid-period via `initiateWithdrawal` + `claimWithdrawal`, which reduces `totalKernelStaked` to zero after the reward period has already started. [3](#0-2) 

The contract itself explicitly acknowledges this in its NatSpec: [4](#0-3) 

> *"If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards for that period will stay locked in the contract."*

The admin function set contains only `setRewardsDuration`, `notifyRewardAmount`, `setWithdrawalDelay`, and `setMaxNumberOfWithdrawalsPerUser`: [5](#0-4) 

None of these allow recovering stranded reward tokens. There is no `recoverERC20`, no `sweepRewards`, and no emergency withdrawal path for the `rewardsToken` balance.

---

### Impact Explanation

**Permanent freezing of unclaimed yield.** Reward tokens transferred into the contract via `notifyRewardAmount` that correspond to the zero-staked interval are irrecoverably locked. They cannot be redistributed to future stakers, returned to the protocol treasury, or recovered by any on-chain mechanism short of a contract upgrade. This directly deprives stakers of yield they are entitled to and constitutes a permanent loss of protocol-owned reward funds.

---

### Likelihood Explanation

**Medium.** The scenario requires all current stakers to exit during an active reward window. This is realistic because:
- Stakers can freely call `initiateWithdrawal` at any time with no restriction tied to the reward period.
- A single large staker exiting (or a coordinated exit) can drive `totalKernelStaked` to zero.
- The reward period can be long (set by `duration`), giving ample time for full unstaking.
- The protocol's own comment acknowledges this as a real risk and relies on an off-chain operational guarantee rather than a code-level fix.

---

### Recommendation

Add an admin-only rescue function that can recover the unallocated reward balance after a reward period ends:

```solidity
function recoverUnallocatedRewards(address recipient) external onlyRole(DEFAULT_ADMIN_ROLE) {
    if (block.timestamp < finishAt) revert RewardDurationNotFinished();
    uint256 unallocated = rewardsToken.balanceOf(address(this))
        - _totalPendingRewards(); // sum of all earned() values
    rewardsToken.safeTransfer(recipient, unallocated);
}
```

Alternatively, track the exact amount of rewards skipped during zero-staked intervals and make them recoverable.

---

### Proof of Concept

1. Admin calls `notifyRewardAmount(1_000e18)` with `duration = 30 days`. `rewardRate = 1_000e18 / 30 days`.
2. Alice is the only staker with `balanceOf[Alice] = 1e18`, `totalKernelStaked = 1e18`.
3. After 1 day, Alice calls `initiateWithdrawal(1e18)`. `totalKernelStaked` drops to `0`.
4. After the `withdrawalDelay`, Alice calls `claimWithdrawal` and receives her KERNEL tokens.
5. For the remaining 29 days, `rewardPerToken()` returns the frozen `rewardPerTokenStored`. No rewards accumulate.
6. `rewardsToken.balanceOf(address(KernelDepositPool))` still holds ≈ `rewardRate * 29 days` tokens.
7. No function exists to recover these tokens. They are permanently locked. [6](#0-5) [7](#0-6) [4](#0-3)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L18-23)
```text
 * @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
 *      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
 *      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
 *      as well as for the entire duration of the reward period. Otherwise, staking just 1 wei by any address
 *      will ensure that the contract never has any unallocated rewards.
 */
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-327)
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
