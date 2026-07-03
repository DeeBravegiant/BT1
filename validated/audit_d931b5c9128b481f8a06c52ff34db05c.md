### Title
Rewards Permanently Locked When `totalKernelStaked` Drops to Zero Mid-Period - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool` guards against starting a reward period with zero stakers, but provides no on-chain protection against `totalKernelStaked` falling to zero **during** an active reward window. When that happens, the `updateReward` modifier advances `updatedAt` while `rewardPerToken()` emits no new rewards, permanently locking the pro-rata reward tokens for that gap inside the contract.

### Finding Description
`rewardPerToken()` short-circuits to `rewardPerTokenStored` whenever `totalKernelStaked == 0`: [1](#0-0) 

The `updateReward` modifier always advances `updatedAt` to `lastTimeRewardApplicable()`, regardless of whether supply is zero: [2](#0-1) 

This means that for any time interval `[T1, T2]` where `totalKernelStaked == 0`, the quantity `rewardRate * (T2 - T1)` worth of reward tokens is silently skipped — `updatedAt` jumps forward, but `rewardPerTokenStored` does not increase. Those tokens remain in the contract with no accounting entry and can never be claimed.

`initiateWithdrawal` freely reduces `totalKernelStaked` to zero during an active reward window: [3](#0-2) 

The only guard in `notifyRewardAmount` checks for zero supply **at the moment of notification**, not throughout the period: [4](#0-3) 

The contract's own NatSpec acknowledges the gap but relies on an off-chain operational assumption rather than on-chain enforcement: [5](#0-4) 

### Impact Explanation
Reward tokens accrued during any zero-supply interval are permanently locked in the contract. No user can claim them, and there is no recovery function. This constitutes **permanent freezing of unclaimed yield** (Medium severity per the allowed impact scope).

### Likelihood Explanation
Any staker can call `initiateWithdrawal` for their full balance at any time. If all stakers withdraw simultaneously (or a single dominant staker exits), `totalKernelStaked` reaches zero mid-period. This requires no special privilege, no front-running, and no external dependency — it is a normal user action available to any depositor.

### Recommendation
When `totalKernelStaked` transitions to zero during an active reward period, track the "dead time" and carry the unallocated rewards forward into the next `notifyRewardAmount` call (analogous to the mitigation in the referenced report). Concretely:

1. Record a `zeroSupplyStart` timestamp whenever `totalKernelStaked` reaches zero inside `initiateWithdrawal`.
2. In `notifyRewardAmount`, add `(block.timestamp - zeroSupplyStart) * rewardRate` to the incoming `receivedAmount` before computing the new `rewardRate`, so stranded rewards are recycled rather than lost.

Alternatively, enforce a minimum stake floor that cannot be withdrawn while a reward period is active.

### Proof of Concept

1. Admin calls `notifyRewardAmount(1000e18)` with `duration = 100 s` → `rewardRate = 10e18/s`, `finishAt = T+100`.
2. At `T+10`, the single staker calls `initiateWithdrawal(fullBalance)` → `totalKernelStaked = 0`.
3. `updateReward` fires: `rewardPerToken()` returns unchanged `rewardPerTokenStored`; `updatedAt` advances to `T+10`.
4. At `T+20`, a new staker deposits. `updateReward` fires again: `rewardPerToken()` now accumulates only from `T+20`, not `T+10`.
5. The `10 s × 10e18 = 100e18` reward tokens for `[T+10, T+20]` are permanently unaccounted for and locked in the contract.
6. After `finishAt`, the total claimable rewards across all users sum to `800e18` instead of `900e18`; the remaining `100e18` (plus any further zero-supply gaps) are irrecoverable.

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-327)
```text
    function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
        if (_amount == 0) revert AmountZero();
        if (balanceOf[msg.sender] < _amount) revert InsufficientStakedBalance();
        if (userWithdrawalIds[msg.sender].length >= maxNumberOfWithdrawalsPerUser) revert WithdrawalLimitReached();

        balanceOf[msg.sender] -= _amount;
        totalKernelStaked -= _amount;

```

**File:** contracts/KERNEL/KernelDepositPool.sol (L408-413)
```text
    function rewardPerToken() public view returns (uint256) {
        if (totalKernelStaked == 0) {
            return rewardPerTokenStored;
        }
        return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
            / totalKernelStaked;
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L569-570)
```text
        // Prevent starting a reward period when no tokens are staked to avoid unallocated rewards
        if (totalKernelStaked == 0) revert NoStakedTokens();
```
