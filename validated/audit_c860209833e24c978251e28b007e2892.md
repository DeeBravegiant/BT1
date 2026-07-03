### Title
Rewards Permanently Frozen When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.rewardPerToken()` contains a guard `if (totalKernelStaked == 0) { return rewardPerTokenStored; }` that silently skips reward accrual when no tokens are staked. Because `updateReward` always advances `updatedAt` to the current timestamp regardless of whether `totalKernelStaked` is zero, any rewards emitted at `rewardRate` during a zero-stake gap are permanently unaccounted for and locked in the contract with no recovery path.

### Finding Description
`rewardPerToken()` returns the stored value unchanged when `totalKernelStaked == 0`:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;   // gap rewards silently dropped
    }
    return rewardPerTokenStored
        + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

The `updateReward` modifier always writes `updatedAt = lastTimeRewardApplicable()`:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    updatedAt = lastTimeRewardApplicable();   // advances even when supply == 0
    ...
}
```

Trace through the gap scenario:

| Time | Event | `totalKernelStaked` | `updatedAt` | Gap rewards |
|------|-------|---------------------|-------------|-------------|
| T=0 | `notifyRewardAmount` | S > 0 | 0 | — |
| T=10 | Alice `initiateWithdrawal(all)` | 0 | 10 | Alice's rewards checkpointed correctly |
| T=30 | Bob `stake(X)` — `updateReward` fires | 0→X | **30** | `rewardPerToken()` returns old stored value; `updatedAt` jumps to 30 |
| T=40 | Bob `getReward()` | X | 40 | Bob earns only T=30→40; T=10→30 rewards are gone |

The rewards emitted from T=10 to T=30 (`rewardRate × 20 seconds`) are permanently stuck in the contract. No admin function, no sweep, no recovery mechanism exists.

`initiateWithdrawal` reduces `totalKernelStaked` immediately at initiation time, not at claim time:

```solidity
balanceOf[msg.sender] -= _amount;
totalKernelStaked -= _amount;          // immediate reduction
```

The mandatory `withdrawalDelay` (up to `MAX_WITHDRAWAL_DELAY = 30 days`) guarantees a window during which `totalKernelStaked` can be zero while `rewardRate > 0`.

The contract's own NatSpec acknowledges the issue but relies on an off-chain operational assumption ("ensuring there are always some tokens staked") rather than any code-level enforcement. The `notifyRewardAmount` guard (`if (totalKernelStaked == 0) revert NoStakedTokens()`) only prevents starting a new reward period with zero stakers; it does not prevent all stakers from exiting during an already-active period.

### Impact Explanation
Reward tokens transferred into the contract via `notifyRewardAmount` and emitted during any zero-stake gap are permanently frozen. There is no `rescueTokens`, no `sweep`, and no way to re-notify the lost amount without the admin injecting fresh tokens for a new period. The lost amount scales with `rewardRate × gap_duration`, which can be substantial over a 30-day withdrawal delay window. This maps to **Medium — Permanent freezing of unclaimed yield**.

### Likelihood Explanation
The scenario is reachable by any single unprivileged staker who is the sole depositor (realistic early in the protocol or after a mass exit). The `withdrawalDelay` up to 30 days makes the gap window large. No code-level guard prevents `totalKernelStaked` from reaching zero during an active reward period. Likelihood is **Medium**.

### Recommendation
Do not advance `updatedAt` when `totalKernelStaked == 0`. Change `rewardPerToken()` to freeze time when the supply is zero, so the gap is replayed once stakers return:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;
    }
    return rewardPerTokenStored
        + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

And in the `updateReward` modifier, only advance `updatedAt` when `totalKernelStaked > 0`:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    if (totalKernelStaked > 0) {
        updatedAt = lastTimeRewardApplicable();
    }
    ...
}
```

This ensures rewards emitted during a zero-stake gap are preserved and distributed to the next staker who enters.

### Proof of Concept

```solidity
// 1. Admin sets duration and notifies reward amount while Alice is staked
kernelDepositPool.setRewardsDuration(30 days);
kernelDepositPool.notifyRewardAmount(30_000e18); // rewardRate = 1000e18/day

// 2. Alice (sole staker) initiates full withdrawal — totalKernelStaked → 0
vm.prank(alice);
kernelDepositPool.initiateWithdrawal(aliceStake);

// 3. 10 days pass with totalKernelStaked == 0
// 10_000e18 reward tokens are emitted but never credited to anyone
vm.warp(block.timestamp + 10 days);

// 4. Bob stakes
vm.prank(bob);
kernelDepositPool.stake(1e18);

// 5. Another 10 days pass
vm.warp(block.timestamp + 10 days);

// 6. Bob claims — earns only ~10_000e18, not ~20_000e18
vm.prank(bob);
kernelDepositPool.getReward();

// 7. The 10_000e18 from the gap is permanently stuck in the contract
// rewardsToken.balanceOf(address(kernelDepositPool)) > 0 with no way to recover
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L17-22)
```text
 * @dev Modified from https://github.com/Synthetixio/synthetix/blob/develop/contracts/StakingRewards.sol
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L566-570)
```text
    function notifyRewardAmount(uint256 _amount) external onlyRole(DEFAULT_ADMIN_ROLE) updateReward(address(0)) {
        if (_amount == 0) revert AmountZero();

        // Prevent starting a reward period when no tokens are staked to avoid unallocated rewards
        if (totalKernelStaked == 0) revert NoStakedTokens();
```
