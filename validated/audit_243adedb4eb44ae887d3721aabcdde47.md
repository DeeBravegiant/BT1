### Title
Rewards Permanently Frozen When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool` distributes rewards using a rate-per-token model. When `totalKernelStaked` drops to zero mid-period, `rewardPerToken()` freezes `rewardPerTokenStored` but the `updateReward` modifier still advances `updatedAt`. All rewards accrued during the zero-staking window are permanently unclaimable.

### Finding Description
`rewardPerToken()` returns the stored value unchanged when `totalKernelStaked == 0`: [1](#0-0) 

The `updateReward` modifier always advances `updatedAt` to `lastTimeRewardApplicable()`, regardless of whether `totalKernelStaked` is zero: [2](#0-1) 

When a new staker eventually calls `stake()`, `updateReward` runs: `rewardPerTokenStored` stays at its old value (no accumulation during the gap), but `updatedAt` is now set to the current time. The reward formula `rewardRate * (lastTimeRewardApplicable() - updatedAt)` then starts from the new `updatedAt`, permanently skipping the zero-staking window. Those reward tokens remain locked in the contract with no mechanism to recover them.

`initiateWithdrawal` has no guard preventing `totalKernelStaked` from reaching zero during an active reward period: [3](#0-2) 

The contract's own NatSpec acknowledges this exact risk but relies on an off-chain operational assumption rather than any on-chain enforcement: [4](#0-3) 

`notifyRewardAmount` correctly blocks starting a reward period with zero stakers, but provides no protection against stakers withdrawing after the period has begun: [5](#0-4) 

### Impact Explanation
Reward tokens sent to the contract via `notifyRewardAmount` that correspond to any time window where `totalKernelStaked == 0` are permanently locked. There is no admin rescue function, no sweep mechanism, and no way to re-distribute them. This is a permanent freezing of unclaimed yield.

**Impact: Medium — Permanent freezing of unclaimed yield.**

### Likelihood Explanation
Any staker can call `initiateWithdrawal` at any time with no restriction tied to active reward periods. A single large staker (or all stakers acting independently for legitimate reasons) can reduce `totalKernelStaked` to zero during a live reward window. No attacker coordination is required; it can happen through normal user behavior. The withdrawal delay does not prevent the `totalKernelStaked` decrement — it happens immediately in `initiateWithdrawal` at line 326, not at `claimWithdrawal`. [6](#0-5) 

### Recommendation
In `initiateWithdrawal`, if `block.timestamp < finishAt` (active reward period), revert or warn when the withdrawal would bring `totalKernelStaked` to zero. Alternatively, in `rewardPerToken()`, do not advance `updatedAt` when `totalKernelStaked == 0` — instead, keep `updatedAt` frozen so the reward debt is preserved and can be distributed once stakers return. The latter approach mirrors the standard Synthetix fix.

### Proof of Concept
1. Admin calls `notifyRewardAmount(1_000e18)` with Alice having staked 100 KERNEL. `rewardRate` is set, `finishAt = block.timestamp + duration`.
2. Alice calls `initiateWithdrawal(100)`. `totalKernelStaked` becomes 0. `balanceOf[Alice]` becomes 0. The `updateReward(Alice)` modifier runs: `rewardPerTokenStored` stays unchanged (since `totalKernelStaked == 0` in `rewardPerToken()`), `updatedAt` is set to `block.timestamp`.
3. Time passes (e.g., half the reward duration). No stakers exist. `rewardRate * elapsed` worth of rewards accrue to nobody.
4. Bob stakes 100 KERNEL. `updateReward(Bob)` runs: `rewardPerTokenStored` is computed as `rewardPerTokenStored + rewardRate * (now - updatedAt) / totalKernelStaked` — but `updatedAt` was already set to the time of Alice's withdrawal, so only the rewards from step 4 onward are counted. The rewards from step 3 are permanently lost in the contract. [7](#0-6)

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L566-570)
```text
    function notifyRewardAmount(uint256 _amount) external onlyRole(DEFAULT_ADMIN_ROLE) updateReward(address(0)) {
        if (_amount == 0) revert AmountZero();

        // Prevent starting a reward period when no tokens are staked to avoid unallocated rewards
        if (totalKernelStaked == 0) revert NoStakedTokens();
```
