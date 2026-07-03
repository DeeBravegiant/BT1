Audit Report

## Title
Reward Tokens Permanently Locked When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
When all stakers call `initiateWithdrawal()` during an active reward window, `totalKernelStaked` immediately drops to zero, halting reward accrual. The reward tokens that would have been distributed during the zero-staker interval are never allocated to any user and cannot be recovered, as `notifyRewardAmount()` does not fold the stranded balance into future reward rates. The contract's own NatSpec acknowledges this behavior but relies entirely on an off-chain operational assumption with no on-chain enforcement.

## Finding Description
`rewardPerToken()` short-circuits when `totalKernelStaked == 0`, returning the stored value unchanged: [1](#0-0) 

The `updateReward` modifier runs before every state-changing call and advances `updatedAt` to `lastTimeRewardApplicable()` regardless of whether any accrual occurred: [2](#0-1) 

`initiateWithdrawal()` decrements `totalKernelStaked` immediately (before the unlock delay), so the last withdrawal sets `totalKernelStaked = 0` and snaps `updatedAt` to the current block: [3](#0-2) 

From that point forward, every call to `updateReward` advances `updatedAt` without accruing any `rewardPerTokenStored`, silently consuming the time budget. When a new staker eventually calls `stake()`, `updateReward` fires before `totalKernelStaked` is incremented — at that instant it is still 0 — so the gap is permanently skipped.

When `notifyRewardAmount()` is called for the next period, neither branch reclaims the stranded balance: [4](#0-3) 

The guard at L570 (`if (totalKernelStaked == 0) revert NoStakedTokens()`) only prevents starting a new period with zero stakers; it does not prevent stakers from withdrawing mid-period, and it does not recover tokens already stranded by a prior zero-staker interval.

The contract's NatSpec explicitly acknowledges this and states the mitigation is purely operational: [5](#0-4) 

No on-chain mechanism enforces that assumption.

## Impact Explanation
Reward tokens emitted during any zero-staker interval are permanently unclaimable by any user and are not recycled into future reward rates. This constitutes **permanent freezing of unclaimed yield** (Medium per the allowed impact scope). The magnitude scales with the duration of the zero-staker gap and the `rewardRate` at the time.

## Likelihood Explanation
`initiateWithdrawal()` is a permissionless, standard user action. All stakers can independently decide to exit — due to market conditions, end-of-season behavior, or loss of confidence — without any coordination or admin involvement. The withdrawal delay (up to 30 days) does not prevent `totalKernelStaked` from hitting zero; it only delays the token transfer. The zero-staker state is reachable through ordinary, independent user behavior.

## Recommendation
In `notifyRewardAmount()`, when `block.timestamp >= finishAt`, compute the unallocated reward balance (contract reward token balance minus the sum of all pending user rewards) and fold it into `receivedAmount` before calculating the new `rewardRate`. Alternatively, add an admin-only sweep function callable only after `finishAt` that transfers any reward surplus (contract balance minus owed rewards) to a treasury address.

## Proof of Concept
1. Admin calls `notifyRewardAmount(1_000e18)` while 1,000 KERNEL are staked → `rewardRate = 1_000e18 / duration`.
2. The sole staker calls `initiateWithdrawal(1_000)` → `updateReward` fires, `rewardPerTokenStored` is updated for the elapsed time, then `totalKernelStaked = 0` and `updatedAt` is snapped to the current block.
3. The full reward window passes. Every block: `rewardPerToken()` returns `rewardPerTokenStored` unchanged; `updatedAt` is not advanced (no calls), so the gap accumulates silently.
4. A new staker calls `stake(1)`. `updateReward` fires: `totalKernelStaked` is still 0 at modifier execution → `rewardPerTokenStored` unchanged, `updatedAt` advanced to now. Then `totalKernelStaked = 1`.
5. After `finishAt`, admin calls `notifyRewardAmount(100e18)` (with `totalKernelStaked > 0`). Branch taken: `rewardRate = 100e18 / duration`. The ~1,000e18 reward tokens from step 1 remain in the contract balance, permanently unclaimable.

**Foundry test plan**: Deploy `KernelDepositPool`, stake, call `notifyRewardAmount`, `vm.warp` past `finishAt` with `totalKernelStaked = 0` throughout, stake 1 wei, call `notifyRewardAmount` again, assert `rewardsToken.balanceOf(address(pool)) > sum of all claimable rewards`.

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L17-23)
```text
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L579-584)
```text
        if (block.timestamp >= finishAt) {
            rewardRate = receivedAmount / duration;
        } else {
            uint256 remaining = (finishAt - block.timestamp) * rewardRate;
            rewardRate = (receivedAmount + remaining) / duration;
        }
```
