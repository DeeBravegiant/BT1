### Title
KERNEL Tokens Sent Directly to `KernelDepositPool` Are Permanently Frozen - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool` tracks staked KERNEL tokens exclusively through internal accounting variables (`totalKernelStaked`, `balanceOf`). KERNEL tokens transferred directly to the contract (bypassing `stake()`) are never reflected in these variables. Because the contract's only KERNEL outflow path is `claimWithdrawal()`, which only disburses amounts recorded in withdrawal structs, and because no admin token-recovery function exists, any KERNEL tokens sent directly are permanently frozen.

### Finding Description
`KernelDepositPool` maintains two accounting variables for staked KERNEL:

- `totalKernelStaked` — updated only in `stake()` and `stakeFor()` (incremented) and `initiateWithdrawal()` (decremented).
- `balanceOf[user]` — updated only in the same three functions. [1](#0-0) 

The sole path for KERNEL tokens to leave the contract is `claimWithdrawal()`, which transfers exactly `withdrawal.amount` — a value that was set when `initiateWithdrawal()` was called, bounded by `balanceOf[msg.sender]`: [2](#0-1) [3](#0-2) 

If a user sends KERNEL tokens directly via `kernelToken.transfer(address(kernelDepositPool), amount)`, those tokens land in the contract's ERC-20 balance but are never added to `balanceOf[user]` or `totalKernelStaked`. The user cannot call `initiateWithdrawal` for them (it checks `balanceOf[msg.sender]`), and no admin function exists to recover them. The contract has no `recoverTokens` or equivalent: [4](#0-3) 

The `notifyRewardAmount` function uses a balance-diff pattern for the *reward* token, not for KERNEL, so it provides no escape path for directly sent KERNEL tokens either: [5](#0-4) 

### Impact Explanation
Any KERNEL tokens sent directly to `KernelDepositPool` — whether by a user who mistakes a direct transfer for a stake, by a script error, or by a third-party integration — are permanently frozen with no recovery path. This satisfies **Critical: Permanent freezing of funds**.

### Likelihood Explanation
Low. The trigger is a user or integration sending KERNEL tokens directly instead of calling `stake()`. This is a realistic mistake for programmatic callers, scripts, or integrations that call `token.transfer(pool, amount)` rather than `token.approve` + `pool.stake(amount)`. The `stakeFor` path (used by `KernelTop100MerkleDistributor`) also calls `safeTransferFrom`, so that path is safe; the risk is purely from direct `transfer` calls. [6](#0-5) 

### Recommendation
Add a token-recovery function restricted to the admin role, similar to the `Recoverable` pattern already present elsewhere in the codebase: [7](#0-6) 

The recovery function should guard against withdrawing tokens that are legitimately owed to stakers (i.e., it should only allow recovering the surplus: `kernelToken.balanceOf(address(this)) - totalKernelStaked - sum(pending withdrawal amounts)`). Alternatively, add a clear NatSpec warning that direct KERNEL transfers are unrecoverable.

### Proof of Concept
1. Alice calls `kernelToken.transfer(address(kernelDepositPool), 1_000e18)` directly.
2. `kernelDepositPool.balanceOf[alice]` remains `0`; `totalKernelStaked` is unchanged.
3. Alice calls `initiateWithdrawal(1_000e18)` → reverts with `InsufficientStakedBalance` because `balanceOf[alice] == 0`.
4. No admin function exists to recover the tokens.
5. The 1 000 KERNEL are permanently locked in the contract. [8](#0-7)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L281-288)
```text
    function stake(uint256 _amount) external nonReentrant updateReward(msg.sender) {
        if (_amount == 0) revert AmountZero();

        balanceOf[msg.sender] += _amount;
        totalKernelStaked += _amount;
        kernelToken.safeTransferFrom(msg.sender, address(this), _amount);

        emit Staked(msg.sender, _amount);
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L296-314)
```text
    function stakeFor(
        address _account,
        uint256 _amount
    )
        external
        nonReentrant
        onlyRole(STAKE_FOR_ROLE)
        updateReward(_account)
    {
        UtilLib.checkNonZeroAddress(_account);

        if (_amount == 0) revert AmountZero();

        balanceOf[_account] += _amount;
        totalKernelStaked += _amount;
        kernelToken.safeTransferFrom(msg.sender, address(this), _amount);

        emit StakedFor(msg.sender, _account, _amount);
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-338)
```text
    function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
        if (_amount == 0) revert AmountZero();
        if (balanceOf[msg.sender] < _amount) revert InsufficientStakedBalance();
        if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();

        balanceOf[msg.sender] -= _amount;
        totalKernelStaked -= _amount;

        // Create a withdrawal record
        uint256 withdrawalId = ++withdrawalCounter;
        uint256 unlockTime = block.timestamp + withdrawalDelay;

        withdrawals[withdrawalId] = Withdrawal({
            user: msg.sender, amount: _amount, unlockTime: unlockTime, claimed: false, withdrawalId: withdrawalId
        });
        userWithdrawalIds[msg.sender].push(withdrawalId);

        emit WithdrawalInitiated(msg.sender, _amount, withdrawalId, unlockTime);
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L374-378)
```text

        // Transfer the KERNEL tokens from contract to user
        kernelToken.safeTransfer(msg.sender, withdrawal.amount);

        emit WithdrawalClaimed(msg.sender, withdrawal.amount, _withdrawalId);
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
