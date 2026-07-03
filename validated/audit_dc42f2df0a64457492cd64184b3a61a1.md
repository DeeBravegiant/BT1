### Title
Smart Contract Stakers Cannot Claim Withdrawn KERNEL Tokens, Causing Permanent Fund Freeze - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary

`KernelDepositPool.claimWithdrawal` enforces a strict `withdrawal.user == msg.sender` check, meaning only the original staker address can retrieve KERNEL tokens after the withdrawal delay. If the staker is a smart contract that lacks the ability to invoke `claimWithdrawal`, the KERNEL tokens become permanently frozen with no admin rescue path.

### Finding Description

The two-step withdrawal flow in `KernelDepositPool` works as follows:

1. A user calls `initiateWithdrawal(uint256 _amount)`, which immediately deducts `_amount` from `balanceOf[msg.sender]` and `totalKernelStaked`, then records a `Withdrawal` struct with `user: msg.sender` and a future `unlockTime`.

2. After the delay, the user must call `claimWithdrawal(uint256 _withdrawalId)` to receive the tokens.

The critical restriction is in `claimWithdrawal`:

```solidity
// contracts/KERNEL/KernelDepositPool.sol lines 351-353
if (withdrawal.user != msg.sender) {
    revert NotYourWithdrawal();
}
```

The tokens are then sent exclusively to `msg.sender`:

```solidity
// line 376
kernelToken.safeTransfer(msg.sender, withdrawal.amount);
```

There is no alternative recovery path. The admin functions (`setWithdrawalDelay`, `setRewardsDuration`, `notifyRewardAmount`, `setMaxNumberOfWithdrawalsPerUser`) provide no mechanism to rescue tokens locked in a withdrawal record. The contract has no emergency withdrawal or admin-override function for individual withdrawal records.

Smart contracts are valid stakers. The `stakeFor` function explicitly supports staking on behalf of any address, including smart contracts. A smart contract that stakes KERNEL (directly via `stake()` or receives a stake via `stakeFor()`) and later calls `initiateWithdrawal()` will have its KERNEL tokens permanently locked if it cannot subsequently call `claimWithdrawal()`.

### Impact Explanation

**Critical — Permanent freezing of funds.**

Once `initiateWithdrawal` is called, the KERNEL tokens are removed from the staker's `balanceOf` and held in the contract. The only recovery path is `claimWithdrawal`, which is gated to `withdrawal.user == msg.sender`. If the staker is an immutable smart contract (e.g., a vault, a protocol integration, a multisig wrapper, or any contract deployed without a `claimWithdrawal` call path), the KERNEL tokens are irrecoverably frozen. No admin function exists to override this.

### Likelihood Explanation

**Medium.** Smart contracts routinely interact with staking protocols — vaults, aggregators, and protocol-owned liquidity contracts are common stakers. The presence of `stakeFor` (which allows privileged accounts to stake on behalf of any address, including contracts) increases the surface area. Any such contract that initiates a withdrawal but lacks a function to call `claimWithdrawal` on `KernelDepositPool` will permanently lose its KERNEL tokens. This is a realistic integration pattern, not a theoretical edge case.

### Recommendation

Remove the `withdrawal.user != msg.sender` restriction and instead send the tokens to `withdrawal.user` regardless of who calls `claimWithdrawal`. This mirrors the fix recommended in the referenced report: allow any account to invoke the claim, while ensuring the assets are always delivered to the recorded beneficiary.

```solidity
function claimWithdrawal(uint256 _withdrawalId) external nonReentrant {
    Withdrawal storage withdrawal = withdrawals[_withdrawalId];

    if (withdrawal.user == address(0)) revert WithdrawalDoesNotExist();
    // REMOVED: if (withdrawal.user != msg.sender) revert NotYourWithdrawal();
    if (block.timestamp < withdrawal.unlockTime) revert WithdrawalNotReady();
    if (withdrawal.claimed) revert WithdrawalAlreadyClaimed();

    withdrawal.claimed = true;

    // Remove from user's list
    uint256[] storage ids = userWithdrawalIds[withdrawal.user];
    for (uint256 i = 0; i < ids.length; ++i) {
        if (ids[i] == _withdrawalId) {
            ids[i] = ids[ids.length - 1];
            ids.pop();
            break;
        }
    }

    // Always send to the recorded beneficiary, not msg.sender
    kernelToken.safeTransfer(withdrawal.user, withdrawal.amount);
    emit WithdrawalClaimed(withdrawal.user, withdrawal.amount, _withdrawalId);
}
```

### Proof of Concept

```
1. Deploy VaultContract — an immutable smart contract that stakes KERNEL but has no
   function to call KernelDepositPool.claimWithdrawal().

2. VaultContract calls KernelDepositPool.stake(1000e18).
   → balanceOf[VaultContract] = 1000e18
   → KERNEL transferred from VaultContract to KernelDepositPool

3. VaultContract calls KernelDepositPool.initiateWithdrawal(1000e18).
   → balanceOf[VaultContract] = 0
   → totalKernelStaked -= 1000e18
   → withdrawals[1] = Withdrawal{user: VaultContract, amount: 1000e18,
       unlockTime: block.timestamp + withdrawalDelay, claimed: false}

4. Time passes; block.timestamp >= unlockTime.

5. No address can call claimWithdrawal(1) on behalf of VaultContract:
   - Any EOA or contract calling claimWithdrawal(1) hits:
       if (withdrawal.user != msg.sender) revert NotYourWithdrawal();
   - VaultContract itself has no function to make this call.

6. Result: 1000e18 KERNEL permanently locked in KernelDepositPool.
   kernelToken.balanceOf(KernelDepositPool) includes these tokens with no
   admin rescue path.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L344-353)
```text
    function claimWithdrawal(uint256 _withdrawalId) external nonReentrant {
        Withdrawal storage withdrawal = withdrawals[_withdrawalId];

        if (withdrawal.user == address(0)) {
            revert WithdrawalDoesNotExist();
        }

        if (withdrawal.user != msg.sender) {
            revert NotYourWithdrawal();
        }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L363-378)
```text
        withdrawal.claimed = true;

        // Remove the withdrawal ID from the user's list of withdrawal IDs
        uint256[] storage userWithdrawalIdsArray = userWithdrawalIds[msg.sender];
        for (uint256 i = 0; i < userWithdrawalIdsArray.length; ++i) {
            if (userWithdrawalIdsArray[i] == _withdrawalId) {
                userWithdrawalIdsArray[i] = userWithdrawalIdsArray[userWithdrawalIdsArray.length - 1];
                userWithdrawalIdsArray.pop();
                break;
            }
        }

        // Transfer the KERNEL tokens from contract to user
        kernelToken.safeTransfer(msg.sender, withdrawal.amount);

        emit WithdrawalClaimed(msg.sender, withdrawal.amount, _withdrawalId);
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L544-620)
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
```
