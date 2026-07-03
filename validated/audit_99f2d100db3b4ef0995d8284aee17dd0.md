### Title
Rewards Permanently Locked When `totalKernelStaked` Drops to Zero Mid-Period - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary

`KernelDepositPool` uses a Synthetix-style staking rewards model. When `totalKernelStaked` reaches zero during an active reward distribution window, the `rewardPerToken()` function freezes `rewardPerTokenStored` at its current value. All reward tokens that should have accrued during the zero-stake interval are permanently locked in the contract with no recovery mechanism.

### Finding Description

The `rewardPerToken()` function handles the zero-supply case by returning the stored value unchanged:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;  // accrual silently stops
    }
    return rewardPerTokenStored
        + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
``` [1](#0-0) 

When `totalKernelStaked == 0`, `updatedAt` is still advanced by the `updateReward` modifier (via `lastTimeRewardApplicable()`), but `rewardPerTokenStored` does not increase. The reward tokens corresponding to `rewardRate * elapsed_time` are never attributed to any account and remain locked in the contract forever.

The `notifyRewardAmount` function guards against starting a new period with zero stakers:

```solidity
if (totalKernelStaked == 0) revert NoStakedTokens();
``` [2](#0-1) 

However, this check only applies at reward period initialization. It does **not** prevent `totalKernelStaked` from dropping to zero mid-period. Any staker can call `initiateWithdrawal()` at any time, which immediately decrements `totalKernelStaked`:

```solidity
balanceOf[msg.sender] -= _amount;
totalKernelStaked -= _amount;
``` [3](#0-2) 

The contract's own NatSpec acknowledges this exact problem but relies on an off-chain operational guarantee rather than on-chain enforcement:

> *"If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards for that period will stay locked in the contract."* [4](#0-3) 

### Impact Explanation

Any reward tokens accrued during the zero-stake interval are permanently locked in the contract. There is no admin rescue function, no sweep mechanism, and no way to roll the lost rewards into a future period. The `rewardsToken` balance of the contract will exceed the sum of all claimable `rewards[user]` values by exactly the amount lost during the zero-stake window. This constitutes **permanent freezing of unclaimed yield**.

### Likelihood Explanation

The trigger is reachable by any ordinary staker. A single user holding 100% of the staked supply can call `initiateWithdrawal(totalBalance)` at any point during an active reward period, immediately dropping `totalKernelStaked` to zero. The withdrawal delay only delays the return of KERNEL tokens; it does not delay the `totalKernelStaked` decrement, which happens immediately at `initiateWithdrawal` time. No admin action or collusion is required.

### Recommendation

When `totalKernelStaked` drops to zero mid-period, the contract should track the "dead time" and either:

1. **Carry forward the unallocated rewards**: When `totalKernelStaked` returns to a nonzero value, recalculate `rewardRate` to include the rewards that were not distributed during the zero-stake gap (similar to how `notifyRewardAmount` handles a mid-period top-up using `remaining`).
2. **Prevent withdrawal to zero during an active period**: Revert `initiateWithdrawal` if it would bring `totalKernelStaked` to zero while `block.timestamp < finishAt`.

Option 1 is preferred as it does not restrict user withdrawals.

### Proof of Concept

1. Admin calls `notifyRewardAmount(1_000_000e18)` with `duration = 30 days`. `rewardRate = 1_000_000e18 / 30 days`. Alice is the only staker with `balanceOf[Alice] = 1000e18`.
2. After 10 days, Alice calls `initiateWithdrawal(1000e18)`. The `updateReward(Alice)` modifier runs first, correctly crediting Alice for 10 days of rewards. Then `totalKernelStaked` becomes 0.
3. 10 more days pass with `totalKernelStaked == 0`. During this window, `rewardRate * 10 days` worth of tokens (≈333,333e18) accrue in the contract but `rewardPerTokenStored` does not advance.
4. Bob stakes 1000e18. The `updateReward(Bob)` modifier calls `rewardPerToken()`, which returns the same `rewardPerTokenStored` as step 2 (since `totalKernelStaked` was 0). Bob's `userRewardPerTokenPaid[Bob]` is set to this value.
5. The remaining 10 days of the period pass normally. Bob earns only 10 days of rewards, not 20.
6. The ≈333,333e18 reward tokens from the zero-stake window are permanently locked in the contract. No account can ever claim them, and there is no admin function to recover them. [5](#0-4) [6](#0-5) [1](#0-0)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L18-22)
```text
 * @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
 *      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
 *      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
 *      as well as for the entire duration of the reward period. Otherwise, staking just 1 wei by any address
 *      will ensure that the contract never has any unallocated rewards.
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L320-326)
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
