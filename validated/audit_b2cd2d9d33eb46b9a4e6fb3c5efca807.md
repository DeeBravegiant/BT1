### Title
Precision Loss in `notifyRewardAmount` Permanently Traps Reward Tokens With No Recovery Path - (File: contracts/KERNEL/KernelDepositPool.sol)

---

### Summary

`KernelDepositPool.notifyRewardAmount()` computes `rewardRate` via integer division, causing a remainder of `receivedAmount % duration` reward tokens to be permanently locked in the contract on every reward notification. The contract provides no sweep or recovery function for `rewardsToken`, making these tokens irrecoverable.

---

### Finding Description

In `notifyRewardAmount`, the reward rate is set by dividing the received token amount by the distribution duration:

```solidity
if (block.timestamp >= finishAt) {
    rewardRate = receivedAmount / duration;
} else {
    uint256 remaining = (finishAt - block.timestamp) * rewardRate;
    rewardRate = (receivedAmount + remaining) / duration;
}
``` [1](#0-0) 

Because Solidity integer division truncates, the actual tokens that will ever be distributed equal `rewardRate * duration`, which is strictly less than `receivedAmount` whenever `receivedAmount % duration != 0`. The difference — `receivedAmount % duration` — is transferred into the contract but never accounted for in any user's claimable rewards. It sits in the contract's `rewardsToken` balance indefinitely.

The entire admin function section of the contract contains only `setRewardsDuration`, `notifyRewardAmount`, `setWithdrawalDelay`, and `setMaxNumberOfWithdrawalsPerUser`. [2](#0-1) 

There is no `sweep`, `recoverTokens`, or `emergencyWithdraw` function for `rewardsToken`. The contract does not inherit from `Recoverable` and has no other mechanism to extract the stranded dust. [3](#0-2) 

---

### Impact Explanation

Every call to `notifyRewardAmount` permanently locks `receivedAmount % duration` reward tokens. The loss compounds across reward cycles. For a 6-decimal reward token (e.g., USDC) with `duration = 604,800` seconds (7 days) and `receivedAmount = 1,000,000` (1 USDC):

- `rewardRate = 1,000,000 / 604,800 = 1` (truncated from ~1.653)
- Tokens distributed: `1 × 604,800 = 604,800`
- Tokens permanently stuck: `395,200` (~39.5% of the reward)

For 18-decimal tokens the per-cycle loss is smaller (< `duration` wei ≈ 604,800 wei per call), but it is still irrecoverable and accumulates across every reward period.

**Impact: Medium — Permanent freezing of unclaimed yield.**

---

### Likelihood Explanation

This is a mathematical certainty, not an edge case. It triggers on every single call to `notifyRewardAmount` unless `receivedAmount` is an exact multiple of `duration`. In practice, reward amounts are chosen by humans and will almost never be exact multiples of the duration in seconds. The loss is guaranteed to occur in normal protocol operation.

---

### Recommendation

Add an admin-only sweep function that transfers any `rewardsToken` balance in excess of the currently owed rewards (i.e., `rewardsToken.balanceOf(address(this)) - totalOwed`) to a designated treasury address. Alternatively, track the cumulative precision dust and allow the admin to recover it explicitly, similar to the `Recoverable` base contract already present in the codebase at `contracts/utils/Recoverable.sol`. [4](#0-3) 

---

### Proof of Concept

1. Admin calls `notifyRewardAmount(1_000_000)` with `duration = 604_800` (7 days) and a 6-decimal reward token.
2. Contract receives `1_000_000` tokens. `rewardRate = 1_000_000 / 604_800 = 1`.
3. Over 7 days, stakers earn `1 × 604_800 = 604_800` tokens total.
4. `395_200` tokens remain in the contract balance but are not tracked in any user's `rewards` mapping and are not included in any future `rewardRate` calculation (they are not part of `remaining` in the `else` branch because `remaining = (finishAt - block.timestamp) * rewardRate` uses the already-truncated `rewardRate`).
5. Admin has no function to call to recover these `395_200` tokens. They are permanently frozen. [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L1-24)
```text
// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.27;

import { AccessControlUpgradeable } from "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";
import { IERC20 } from "@openzeppelin/contracts/interfaces/IERC20.sol";
import { Initializable } from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import {
    ReentrancyGuardUpgradeable
} from "@openzeppelin/contracts-upgradeable/security/ReentrancyGuardUpgradeable.sol";
import { SafeERC20 } from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

import { UtilLib } from "contracts/utils/UtilLib.sol";

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
contract KernelDepositPool is Initializable, AccessControlUpgradeable, ReentrancyGuardUpgradeable {
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
