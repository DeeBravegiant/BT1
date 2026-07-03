### Title
Permanent Locking of Undistributed Rewards When `totalKernelStaked` Drops to Zero Mid-Period — (`contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

Any staker holding 100% of `totalKernelStaked` (or a coordinated group holding 100%) can call `initiateWithdrawal` for their full balance during an active reward period, driving `totalKernelStaked` to zero. Once that happens, `rewardPerToken()` freezes at `rewardPerTokenStored` for all future calls, and the remaining `(finishAt - block.timestamp) * rewardRate` reward tokens are permanently locked in the contract with no recovery path.

---

### Finding Description

`rewardPerToken()` short-circuits to `rewardPerTokenStored` whenever `totalKernelStaked == 0`: [1](#0-0) 

`initiateWithdrawal()` decrements `totalKernelStaked` unconditionally after the `updateReward` modifier snapshots the current accumulated value: [2](#0-1) 

The `updateReward` modifier runs first, correctly crediting the exiting staker's earned rewards up to that moment, then `totalKernelStaked` is set to zero: [3](#0-2) 

`notifyRewardAmount()` guards against starting a period with zero staked tokens, but provides no protection against `totalKernelStaked` reaching zero *after* a period has started: [4](#0-3) 

There is no `recoverERC20`, `sweep`, or any other admin escape hatch in the contract — the file ends at line 621 with only `setMaxNumberOfWithdrawalsPerUser`: [5](#0-4) 

The contract's own NatSpec explicitly acknowledges this design limitation and states it is mitigated *operationally* (not on-chain): [6](#0-5) 

The operational mitigation ("ensuring there are always some tokens staked for the entire duration") is not enforced by any code. There is no lock-up, no minimum-stake invariant during active periods, and no on-chain mechanism preventing a staker from exiting mid-period.

---

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

The remaining reward tokens for the rest of the period become permanently unclaimable and unrecoverable. The `rewardsToken` balance sits in `KernelDepositPool` forever. No staker can earn those tokens (since `balanceOf` is zero for all), and no admin function can retrieve them.

---

### Likelihood Explanation

The scenario requires a single staker holding 100% of `totalKernelStaked`, or a coordinated group. This is realistic in early deployment (one bootstrap staker before others join) or in a low-participation environment. No privileged role is needed — `initiateWithdrawal` is a public user function. The attacker also recovers their own principal after `withdrawalDelay`, so there is no cost to triggering this.

---

### Recommendation

1. **Add a `recoverERC20` admin function** that allows recovery of *excess* reward tokens (i.e., `rewardsToken.balanceOf(address(this))` minus the sum of all `rewards[user]` mappings), so stranded rewards can be rescued and re-notified.
2. **Alternatively**, enforce on-chain that `totalKernelStaked` cannot reach zero during an active reward period by reverting `initiateWithdrawal` if `block.timestamp < finishAt && totalKernelStaked - _amount == 0`.
3. The operational note in the NatSpec is insufficient as a security control.

---

### Proof of Concept

```solidity
// 1. Admin sets duration and notifies reward amount (totalKernelStaked > 0 required)
pool.setRewardsDuration(7 days);
rewardsToken.approve(address(pool), 1000e18);
pool.notifyRewardAmount(1000e18);
// rewardRate = 1000e18 / 7 days

// 2. Attacker (sole staker) initiates withdrawal for full balance mid-period
// updateReward runs first: rewardPerTokenStored updated, attacker's earned() credited
pool.initiateWithdrawal(attackerFullBalance);
// Now totalKernelStaked == 0

// 3. rewardPerToken() is now frozen
assert(pool.rewardPerToken() == pool.rewardPerTokenStored()); // always true hereafter

// 4. After finishAt, remaining rewards are locked
// locked = (finishAt - timestampOfWithdrawal) * rewardRate
// No function exists to recover them
assert(rewardsToken.balanceOf(address(pool)) > 0); // permanently stuck
```

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L606-621)
```text
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
