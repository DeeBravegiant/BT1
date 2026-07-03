### Title
Rewards Permanently Frozen When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
In `KernelDepositPool.sol`, the `updateReward` modifier unconditionally advances `updatedAt` to `lastTimeRewardApplicable()` even when `totalKernelStaked == 0`. If all stakers exit during an active reward period, the reward tokens accrued during the empty interval are permanently frozen in the contract with no recovery path.

### Finding Description
The `updateReward` modifier executes two assignments unconditionally:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    updatedAt = lastTimeRewardApplicable();   // ← always advances
    ...
}
``` [1](#0-0) 

`rewardPerToken()` correctly short-circuits when the pool is empty:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;   // no accumulation
    }
    ...
}
``` [2](#0-1) 

However, `updatedAt` is still advanced to the current time. When the next staker calls `stake()` (triggering `updateReward`), the formula `rewardRate * (lastTimeRewardApplicable() - updatedAt)` uses the newly advanced `updatedAt`, so the rewards that accrued during the entire empty interval are silently skipped and never added to `rewardPerTokenStored`. Those reward tokens remain in the contract forever.

`notifyRewardAmount` does guard against starting a reward period on an empty pool:

```solidity
if (totalKernelStaked == 0) revert NoStakedTokens();
``` [3](#0-2) 

But this guard only prevents *initiating* a new period on an empty pool. It does not protect against the pool becoming empty *after* a reward period has already started. Once `totalKernelStaked` reaches zero mid-period, the rewards for that gap are unrecoverable.

`KernelDepositPool` does not inherit `Recoverable`, has no `recoverTokens`, `emergencyWithdraw`, or any equivalent function, so the frozen reward tokens cannot be retrieved by anyone. [4](#0-3) 

### Impact Explanation
Reward tokens transferred into the contract via `notifyRewardAmount` are permanently frozen for the duration of any interval where `totalKernelStaked == 0`. There is no admin escape hatch in `KernelDepositPool` to recover them. This constitutes **permanent freezing of unclaimed yield** (Medium severity per the allowed impact scope).

### Likelihood Explanation
Any staker can call `initiateWithdrawal` at any time; this immediately decrements `totalKernelStaked`:

```solidity
balanceOf[msg.sender] -= _amount;
totalKernelStaked -= _amount;
``` [5](#0-4) 

If all stakers exit during an active reward period (a realistic scenario during market stress or migration), the pool reaches `totalKernelStaked == 0` while `block.timestamp < finishAt`. The next `stake()` call then silently advances `updatedAt` past the empty window. No privileged action or attacker coordination is required — ordinary user withdrawals are sufficient.

### Recommendation
In the `updateReward` modifier, only advance `updatedAt` when `totalKernelStaked > 0`:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    if (totalKernelStaked > 0) {
        updatedAt = lastTimeRewardApplicable();
    }
    ...
}
```

This mirrors the fix recommended in the external report and is the standard pattern used in audited Synthetix-style staking contracts. It ensures that the time window during which no tokens are staked does not consume the reward budget.

### Proof of Concept

1. Admin calls `notifyRewardAmount(1000e18)` with `totalKernelStaked > 0`. `finishAt = T + duration`, `updatedAt = T`, `rewardRate = 1000e18 / duration`.
2. All stakers call `initiateWithdrawal` for their full balances. After the last withdrawal, `totalKernelStaked == 0`. `updatedAt` was last set to the time of the last withdrawal, say `T+100`.
3. No one stakes for 500 seconds. Rewards worth `rewardRate * 500` tokens accrue but are unassignable.
4. At `T+600`, Alice calls `stake(1)`. `updateReward` fires:
   - `rewardPerToken()` returns `rewardPerTokenStored` unchanged (since `totalKernelStaked == 0` before Alice's balance is added).
   - `updatedAt` is set to `T+600`.
5. The `rewardRate * 500` tokens (step 3) are now permanently skipped — `updatedAt` jumped from `T+100` to `T+600` without any corresponding increase in `rewardPerTokenStored`. Those tokens sit in the contract with no mechanism to distribute or recover them. [1](#0-0) [2](#0-1)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L1-30)
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
    using SafeERC20 for IERC20;

    /*//////////////////////////////////////////////////////////////
                            CONSTANTS
    //////////////////////////////////////////////////////////////*/

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L325-326)
```text
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L569-570)
```text
        // Prevent starting a reward period when no tokens are staked to avoid unallocated rewards
        if (totalKernelStaked == 0) revert NoStakedTokens();
```
