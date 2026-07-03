Audit Report

## Title
`updateReward` Unconditionally Advances `updatedAt` During Zero-Supply Periods, Permanently Freezing Reward Tokens - (`contracts/KERNEL/KernelDepositPool.sol`)

## Summary
The `updateReward` modifier in `KernelDepositPool` always sets `updatedAt = lastTimeRewardApplicable()` regardless of whether `totalKernelStaked` is zero. Because `rewardPerToken()` freezes the accumulator when supply is zero, any rewards emitted during a zero-supply window are permanently unclaimable — the timestamp gap is consumed without distributing the corresponding tokens.

## Finding Description
The `updateReward` modifier at L232–242 unconditionally advances `updatedAt`:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    updatedAt = lastTimeRewardApplicable();   // always executes
    ...
}
``` [1](#0-0) 

`rewardPerToken()` at L408–414 short-circuits when `totalKernelStaked == 0`, returning the stored value without advancing the accumulator:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
``` [2](#0-1) 

The exploit path:
1. Alice is the sole staker. She calls `initiateWithdrawal(totalBalance)`, which triggers `updateReward` (correctly snapshotting her earned rewards) and then decrements `totalKernelStaked` to zero. [3](#0-2) 
2. Time passes. `rewardRate` continues emitting tokens, but no accumulator advances because `totalKernelStaked == 0`.
3. Bob calls `stake()`. The `updateReward` modifier fires **before** Bob's balance is added, so `totalKernelStaked` is still 0. `rewardPerToken()` returns the frozen `rewardPerTokenStored`, but `updatedAt` is unconditionally set to `lastTimeRewardApplicable()`, jumping past the entire zero-supply gap.
4. All rewards emitted during the zero-supply window are now permanently unaccountable: `updatedAt` has moved forward, so the future `(lastTimeRewardApplicable() - updatedAt)` calculation will never include that interval again.

The `notifyRewardAmount` guard at L570 only prevents starting a new reward period with zero stakers; it does not protect against a mid-period zero-supply window caused by a full withdrawal. [4](#0-3) 

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.**

Rewards emitted during any zero-supply window are permanently stranded in the contract. No user ever earned them (supply was zero), so they are not claimable by anyone. The contract's reward token balance grows beyond what is distributable, and the deficit is borne by the protocol's intended emission schedule. This matches the allowed impact: *Medium. Permanent freezing of unclaimed yield.*

The submitted claim characterizes this as "High — Theft of unclaimed yield," but no user's already-accrued yield is taken from them. The frozen tokens were never attributed to any account, making this a permanent freeze rather than a theft.

## Likelihood Explanation
Any unprivileged user who holds the entire staked supply can trigger this by calling `initiateWithdrawal` for their full balance and waiting any duration before a new staker enters. No special role, governance action, or external dependency is required. The scenario is realistic whenever the pool has a single dominant staker or temporarily empties between reward epochs. It is repeatable across multiple reward periods.

## Recommendation
Gate the `updatedAt` advance on a non-zero supply inside the `updateReward` modifier:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    if (totalKernelStaked > 0) {
        updatedAt = lastTimeRewardApplicable();
    }
    if (_account != address(0)) {
        rewards[_account] = earned(_account);
        userRewardPerTokenPaid[_account] = rewardPerTokenStored;
    }
    _;
}
```

This freezes `updatedAt` during zero-supply periods so that when supply returns, the elapsed time is correctly included in the next `rewardPerToken()` calculation. Alternatively, re-queue the unallocated rewards back into the reward rate inside `notifyRewardAmount` or a dedicated recovery function.

## Proof of Concept
Setup: `finishAt = t+15`, `rewardRate = 100`, `rewardPerTokenStored = 0`, `updatedAt = t+0`.

| Time | Action | `rewardPerTokenStored` | `updatedAt` | `totalKernelStaked` |
|------|--------|----------------------|-------------|---------------------|
| t+0  | Alice stakes 100 | 0 | t+0 | 100 |
| t+5  | Alice calls `initiateWithdrawal(100)` | `5e18` | t+5 | 0 |
| t+10 | Bob calls `stake(100)` → `updateReward` fires with supply=0 | `5e18` (frozen) | **t+10** (gap consumed) | 100 |
| t+15 | Bob calls `getReward()` | `10e18` | t+15 | 100 |

- Alice claims: `100 × (5e18 − 0) / 1e18 = 500`
- Bob claims: `100 × (10e18 − 5e18) / 1e18 = 500`
- Total distributed: **1000**
- Total generated: `15 × 100 = 1500`
- **Permanently frozen: 500** (rewards from t+5 → t+10)

A Foundry test can reproduce this by deploying `KernelDepositPool`, staking, withdrawing the full supply, warping time, staking again, and asserting that `rewardsToken.balanceOf(pool) > totalClaimable`.

### Citations

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
