### Title
Reward Token Dust Permanently Locked Due to Integer Division Truncation With No Recovery Mechanism - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.notifyRewardAmount` computes `rewardRate` via integer division, permanently stranding the truncated remainder in the contract. Unlike other contracts in the codebase (e.g., `Recoverable.sol`, `SonicChainNativeTokenBridge.sol`), `KernelDepositPool` has no `recoverTokens` or equivalent function for the `rewardsToken`, so the dust is irrecoverable.

### Finding Description
In `notifyRewardAmount`, the reward rate is computed as:

```solidity
// Line 580
rewardRate = receivedAmount / duration;
// Line 582-583 (mid-period top-up)
uint256 remaining = (finishAt - block.timestamp) * rewardRate;
rewardRate = (receivedAmount + remaining) / duration;
```

Both branches use integer division. The total tokens that will ever be distributed is `rewardRate * duration`, which is strictly less than `receivedAmount` whenever `receivedAmount % duration != 0`. The difference — `receivedAmount % duration` — is deposited into the contract but never emitted to any staker and never returned to the caller.

The contract's entire admin function set is:
- `setRewardsDuration`
- `notifyRewardAmount`
- `setWithdrawalDelay`
- `setMaxNumberOfWithdrawalsPerUser`

None of these allow withdrawing `rewardsToken`. There is no `recoverTokens`, no `sweep`, and no `withdrawTokens` function for the rewards token. The dust accumulates with every `notifyRewardAmount` call and is permanently frozen.

### Impact Explanation
Every call to `notifyRewardAmount` with an amount not perfectly divisible by `duration` leaves `receivedAmount % duration` wei of `rewardsToken` permanently locked. Over multiple reward periods this compounds. The tokens are yield owed to stakers (or at minimum to the protocol) that can never be claimed or recovered by anyone — matching **Medium: Permanent freezing of unclaimed yield**.

### Likelihood Explanation
This is near-certain in practice. `duration` is set in seconds (e.g., 30 days = 2,592,000 seconds). Any reward amount that is not an exact multiple of `duration` — which is virtually every real-world deposit — produces a non-zero remainder. The effect is deterministic and triggered by the normal admin workflow of funding rewards.

### Recommendation
Add a `recoverRewardDust` (or general `recoverTokens`) function restricted to `DEFAULT_ADMIN_ROLE` that allows sweeping the difference between the contract's `rewardsToken` balance and the amount still owed to stakers (`rewardRate * (finishAt - block.timestamp)` plus all pending `rewards[user]` balances). Alternatively, track the exact undistributed dust at `notifyRewardAmount` time and return it immediately to the caller.

### Proof of Concept

**Root cause — integer division in `notifyRewardAmount`:** [1](#0-0) 

**Dust is irrecoverable — the complete admin function set contains no token-recovery path:** [2](#0-1) 

**Contrast: other contracts in the same repo expose `recoverTokens` for exactly this scenario:** [3](#0-2) 

**Numeric example:**
- `duration = 2_592_000` (30 days in seconds)
- Admin calls `notifyRewardAmount(10_000_001)`
- `rewardRate = 10_000_001 / 2_592_000 = 3` (truncated)
- Total distributed = `3 * 2_592_000 = 7_776_000`
- Dust locked = `10_000_001 - 7_776_000 = 2_224_001` tokens — permanently frozen with no recovery path.

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

**File:** contracts/utils/Recoverable.sol (L41-57)
```text
    function recoverTokens(
        address tokenAddress,
        address recipient,
        uint256 amount
    )
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
    {
        UtilLib.checkNonZeroAddress(tokenAddress);
        UtilLib.checkNonZeroAddress(recipient);
        if (amount == 0) revert ZeroAmount();
        if (IERC20(tokenAddress).balanceOf(address(this)) < amount) revert InsufficientBalance();

        IERC20(tokenAddress).safeTransfer(recipient, amount);

        emit TokensRecovered(tokenAddress, recipient, amount);
    }
```
