### Title
Rewards Permanently Locked When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
In `KernelDepositPool`, when `totalKernelStaked` reaches zero during an active reward distribution window, all reward tokens that should have accrued during the zero-stake interval are permanently locked in the contract with no recovery mechanism. This is the direct analog of the MaiaDAO cycle-skip issue: just as skipped cycles cause rewards to be silently discarded, a zero-stake gap causes the same silent discard of accrued rewards.

### Finding Description
The `rewardPerToken()` function implements a Synthetix-style continuous accrual model:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;
    }
    return rewardPerTokenStored
        + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
``` [1](#0-0) 

When `totalKernelStaked == 0`, the function short-circuits and returns `rewardPerTokenStored` unchanged. The `updatedAt` timestamp is only advanced inside the `updateReward` modifier, which fires on user-triggered actions (`stake`, `initiateWithdrawal`, `getReward`). If no user action occurs while `totalKernelStaked == 0`, `updatedAt` is never advanced, and the elapsed time (`lastTimeRewardApplicable() - updatedAt`) accumulates silently. When the next user eventually stakes, `rewardPerToken()` is called again — but because `totalKernelStaked` was 0 during the gap, the branch still returns the stale `rewardPerTokenStored`, discarding all rewards that should have accrued during the zero-stake window. [2](#0-1) 

The `initiateWithdrawal` function is callable by any staker and reduces `totalKernelStaked` by the withdrawn amount: [3](#0-2) 

If the last remaining staker calls `initiateWithdrawal`, `totalKernelStaked` drops to zero. All reward tokens that should have accrued for the remainder of the active window are permanently locked in the contract. There is no admin sweep, rescue, or rollover function anywhere in the contract.

The contract's own NatSpec acknowledges this:

> *"If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards for that period will stay locked in the contract."* [4](#0-3) 

The stated mitigation is purely deployment-level ("ensuring there are always some tokens staked"). The code provides no enforcement: no minimum-stake floor, no lock preventing full withdrawal during an active reward period, and no admin recovery path.

### Impact Explanation
Reward tokens deposited via `notifyRewardAmount` are permanently irrecoverable once `totalKernelStaked` reaches zero during the reward window. The contract holds the tokens but no user can ever claim them, and no admin function can extract them. This is **permanent freezing of unclaimed yield**.

### Likelihood Explanation
The scenario requires all stakers to exit during an active reward period. This is realistic: if KERNEL token value declines sharply, rational stakers exit simultaneously. The last staker to call `initiateWithdrawal` causes all remaining rewards to be permanently locked — a loss that falls on the protocol, not on the exiting user. The function is permissionless and has no guard against this outcome.

### Recommendation
Add an admin-callable `rescueUnallocatedRewards(address recipient)` function that transfers excess reward tokens when `totalKernelStaked == 0` and the reward period has ended (i.e., `block.timestamp >= finishAt`). Alternatively, track the cumulative "dead time" during which `totalKernelStaked == 0` and deduct it from the effective reward window so that unallocated rewards roll forward into the next period rather than being silently discarded.

### Proof of Concept
1. Admin calls `notifyRewardAmount(1_000e18)` with `duration = 7 days`; `rewardRate = ~1_653e12` tokens/second, `finishAt = now + 7 days`.
2. Alice is the sole staker: `balanceOf[Alice] = 100e18`, `totalKernelStaked = 100e18`.
3. After 3.5 days Alice calls `initiateWithdrawal(100e18)`. The `updateReward` modifier fires, advancing `rewardPerTokenStored` and setting `updatedAt = block.timestamp`. `totalKernelStaked` becomes 0.
4. For the remaining 3.5 days, every call to `rewardPerToken()` returns the stale `rewardPerTokenStored` (the `totalKernelStaked == 0` branch). `updatedAt` is never advanced.
5. Alice claims her ~500e18 rewards (for the first 3.5 days). The remaining ~500e18 tokens sit in the contract forever — no user can claim them, no admin can recover them. [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L14-23)
```text
/**
 * @title Kernel Staking Rewards Contract
 * @dev Implements a basic staking mechanism with rewards
 * @dev Modified from https://github.com/Synthetixio/synthetix/blob/develop/contracts/StakingRewards.sol
 * @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
 *      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
 *      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
 *      as well as for the entire duration of the reward period. Otherwise, staking just 1 wei by any address
 *      will ensure that the contract never has any unallocated rewards.
 */
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L232-242)
```text
    modifier updateReward(address _account) {
        rewardPerTokenStored = rewardPerToken();
        updatedAt = lastTimeRewardApplicable();

        if (_account != address(0)) {
            rewards[_account] = earned(_account);
            userRewardPerTokenPaid[_account] = rewardPerTokenStored;
        }

        _;
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
