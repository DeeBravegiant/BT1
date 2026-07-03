### Title
Permanent Freezing of Unclaimed Yield When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary

`KernelDepositPool` uses a Synthetix-style reward accounting model. When `totalKernelStaked` is zero, `rewardPerToken()` returns the frozen `rewardPerTokenStored` value unchanged, causing all rewards that accrue during that period to be permanently locked in the contract. Because `initiateWithdrawal` immediately decrements `totalKernelStaked` and the withdrawal delay can be up to 30 days, any user who is the last active staker can — intentionally or not — cause a multi-day gap where all emitted rewards are irrecoverable.

### Finding Description

In `rewardPerToken()`, when `totalKernelStaked == 0`, the function returns the stored value without advancing it:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;  // frozen — rewards emitted during this gap are lost
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
``` [1](#0-0) 

`initiateWithdrawal` immediately decrements `totalKernelStaked` at the moment of the call, before the withdrawal delay elapses:

```solidity
balanceOf[msg.sender] -= _amount;
totalKernelStaked -= _amount;
``` [2](#0-1) 

The withdrawal delay can be set up to `MAX_WITHDRAWAL_DELAY = 30 days`: [3](#0-2) 

`claimWithdrawal` does not re-increment `totalKernelStaked`; it only transfers tokens back. So the gap between `initiateWithdrawal` and a subsequent `stake` call is the window during which all rewards are permanently lost.

The contract's own NatSpec acknowledges this risk but relies entirely on an operational mitigation (admin ensures tokens are always staked), with no on-chain enforcement: [4](#0-3) 

### Impact Explanation

Any rewards emitted by `rewardRate` during the period when `totalKernelStaked == 0` are permanently locked in the contract. No staker — present or future — can ever claim them. With `rewardRate` set to a meaningful value and a withdrawal delay of up to 30 days, the locked amount can be substantial (e.g., `rewardRate * 30 days` worth of reward tokens). This matches the **Medium — Permanent freezing of unclaimed yield** impact category.

### Likelihood Explanation

The trigger is a call to `initiateWithdrawal` by the last remaining staker. This can happen:
- Naturally, if all stakers decide to exit simultaneously (e.g., during a market downturn or protocol migration).
- Deliberately, by a griefing attacker who is the sole staker and calls `initiateWithdrawal` to zero out `totalKernelStaked`, waits out the delay, then re-stakes — permanently destroying the rewards that accrued during the gap.

The entry path (`initiateWithdrawal`) is public and requires no special role. The `withdrawalDelay` is admin-configurable up to 30 days, making the potential loss window large. [5](#0-4) 

### Recommendation

When `totalKernelStaked` drops to zero, pause the reward clock by updating `updatedAt` to `lastTimeRewardApplicable()` at that moment (already done via `updateReward`), and ensure that when the next staker arrives, `rewardPerTokenStored` is not retroactively inflated. The standard fix is to track the "zero-staked gap" and either:
1. Extend `finishAt` by the duration of the zero-staked gap so rewards are not lost, or
2. Enforce at the code level that `initiateWithdrawal` reverts if it would reduce `totalKernelStaked` to zero while a reward period is active (`block.timestamp < finishAt`), forcing the last staker to wait until the reward period ends before withdrawing.

### Proof of Concept

1. Admin calls `notifyRewardAmount` with `rewardRate = R` and `duration = D`. `finishAt = now + D`.
2. Alice is the only staker with `totalKernelStaked = X`.
3. Alice calls `initiateWithdrawal(X)`. `totalKernelStaked` becomes 0 immediately. `updateReward(alice)` snapshots her earned rewards correctly up to this point.
4. For the next `withdrawalDelay` seconds (up to 30 days), `rewardPerToken()` returns the frozen `rewardPerTokenStored`. All `R * withdrawalDelay` reward tokens emitted during this window are untracked and permanently locked.
5. Alice calls `claimWithdrawal` and gets her KERNEL back. She re-stakes. Future rewards resume normally, but the gap rewards are gone forever.
6. The `rewardsToken.balanceOf(address(KernelDepositPool))` will permanently exceed the sum of all claimable rewards by `R * withdrawalDelay`. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L18-22)
```text
 * @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
 *      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
 *      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
 *      as well as for the entire duration of the reward period. Otherwise, staking just 1 wei by any address
 *      will ensure that the contract never has any unallocated rewards.
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L35-35)
```text
    uint256 public constant MAX_WITHDRAWAL_DELAY = 30 days;
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L344-379)
```text
    function claimWithdrawal(uint256 _withdrawalId) external nonReentrant {
        Withdrawal storage withdrawal = withdrawals[_withdrawalId];

        if (withdrawal.user == address(0)) {
            revert WithdrawalDoesNotExist();
        }

        if (withdrawal.user != msg.sender) {
            revert NotYourWithdrawal();
        }

        if (block.timestamp < withdrawal.unlockTime) {
            revert WithdrawalNotReady();
        }

        if (withdrawal.claimed) {
            revert WithdrawalAlreadyClaimed();
        }

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
    }
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
