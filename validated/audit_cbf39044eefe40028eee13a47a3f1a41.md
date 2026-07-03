### Title
Loss of Rewards When `totalKernelStaked == 0` During Active Reward Period - (File: `contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

`KernelDepositPool.rewardPerToken()` silently skips reward accumulation when `totalKernelStaked == 0`. If all stakers withdraw during an active reward window, the rewards emitted during the zero-staked interval are permanently locked in the contract with no recovery path.

---

### Finding Description

`KernelDepositPool` implements a Synthetix-style staking rewards mechanism. The `rewardPerToken()` function is the core accumulator:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;   // ← accumulator frozen; rewards silently lost
    }
    return rewardPerTokenStored
        + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
``` [1](#0-0) 

The `updateReward` modifier calls `rewardPerToken()` and then unconditionally advances `updatedAt` to `lastTimeRewardApplicable()`:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    updatedAt = lastTimeRewardApplicable();
    ...
}
``` [2](#0-1) 

When `totalKernelStaked == 0`, `rewardPerTokenStored` is returned unchanged, but `updatedAt` is still advanced. The time gap during which no one was staked is permanently consumed: `rewardRate * (gap_duration)` worth of reward tokens are never credited to any user and cannot be recovered.

The only guard against this is in `notifyRewardAmount`, which reverts if `totalKernelStaked == 0` at the moment a new reward period is *started*:

```solidity
if (totalKernelStaked == 0) revert NoStakedTokens();
``` [3](#0-2) 

This check does **not** prevent all stakers from withdrawing *after* a reward period has already begun. The contract's own NatSpec acknowledges the risk but relies on an off-chain operational assumption rather than an on-chain enforcement:

> *"If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards for that period will stay locked in the contract."* [4](#0-3) 

The withdrawal path that drains `totalKernelStaked` to zero is `initiateWithdrawal()`, which is callable by any unprivileged staker:

```solidity
function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
    ...
    balanceOf[msg.sender] -= _amount;
    totalKernelStaked -= _amount;
    ...
}
``` [5](#0-4) 

---

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

Any rewards emitted by `rewardRate` during the period when `totalKernelStaked == 0` are permanently locked in the contract. There is no admin sweep, no recovery function, and no mechanism to roll the lost rewards into a future period. The reward tokens are irrecoverably stranded.

---

### Likelihood Explanation

The scenario requires the last staker to withdraw during an active reward window. This is realistic:

- Reward periods can be long (set by admin via `setRewardsDuration`; no upper bound enforced).
- Any single staker who holds 100% of `totalKernelStaked` can trigger this by calling `initiateWithdrawal` for their full balance.
- No on-chain mechanism prevents this; the mitigation described in the NatSpec is purely operational.
- The attacker does not need to be malicious — ordinary user behavior (withdrawing during a reward period) is sufficient to trigger the loss.

---

### Recommendation

1. **Skip `updatedAt` advancement when `totalKernelStaked == 0`** — do not advance `updatedAt` in the `updateReward` modifier when the supply is zero, so the time gap is not consumed:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    if (totalKernelStaked > 0) {
        updatedAt = lastTimeRewardApplicable();
    }
    ...
}
```

2. **Or add an admin recovery function** — allow the admin to reclaim reward tokens that were emitted during a zero-staked period and re-inject them via `notifyRewardAmount`.

---

### Proof of Concept

1. Admin calls `notifyRewardAmount(1_000e18)` with `totalKernelStaked = 100e18` and `duration = 30 days`. `rewardRate = 1_000e18 / 30 days ≈ 385e12 tokens/sec`.
2. After 10 days, the sole staker calls `initiateWithdrawal(100e18)`. `updateReward` runs: `rewardPerTokenStored` is updated correctly for the first 10 days, `updatedAt` is set to `block.timestamp`. `totalKernelStaked` becomes `0`.
3. 10 more days pass with no stakers. A new user calls `stake(1e18)`. `updateReward` fires: `rewardPerToken()` returns `rewardPerTokenStored` unchanged (because `totalKernelStaked == 0`), but `updatedAt` advances by 10 days. The `rewardRate * 10 days ≈ 333e18` tokens emitted during the gap are permanently unaccounted for.
4. The remaining ~333e18 reward tokens sit in the contract balance forever. No user can claim them; no admin function can recover them. [1](#0-0) [6](#0-5)

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
