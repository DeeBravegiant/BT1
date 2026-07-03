### Title
Reward tokens permanently frozen when `totalKernelStaked` drops to zero during an active reward period - (File: `contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

`KernelDepositPool` is a Synthetix-style staking contract where stakers earn reward tokens over a configured `duration`. When all stakers withdraw during an active reward period, `totalKernelStaked` drops to zero. The `updateReward` modifier unconditionally advances `updatedAt` to `lastTimeRewardApplicable()` even when `totalKernelStaked == 0`, while `rewardPerToken()` returns `rewardPerTokenStored` unchanged in that state. The reward tokens that were supposed to be distributed during the unstaked gap are permanently frozen in the contract with no recovery path.

---

### Finding Description

The `updateReward` modifier runs on every user-facing function (`stake`, `stakeFor`, `initiateWithdrawal`, `getReward`, `notifyRewardAmount`):

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();   // returns stored value unchanged if totalKernelStaked == 0
    updatedAt = lastTimeRewardApplicable();    // always advances, regardless of totalKernelStaked
    ...
}
```

And `rewardPerToken()`:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;           // no accumulation
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

The step-by-step failure path:

1. Admin calls `notifyRewardAmount(amount)` → `rewardRate = amount / duration`, `finishAt = now + duration`, `updatedAt = now`.
2. Stakers stake KERNEL tokens.
3. All stakers call `initiateWithdrawal`. On the last withdrawal, `updateReward(lastStaker)` runs while `totalKernelStaked > 0`, so rewards are correctly snapshotted and `updatedAt` is set to the current timestamp. Then `totalKernelStaked` becomes 0.
4. Time passes. `rewardRate` continues to imply token emission, but no one is staked.
5. A new staker calls `stake`. `updateReward(newStaker)` runs:
   - `rewardPerToken()` returns `rewardPerTokenStored` unchanged (because `totalKernelStaked == 0` at this point — the new stake has not yet been credited).
   - `updatedAt = lastTimeRewardApplicable()` advances past the entire gap.
6. The rewards for the gap period — `rewardRate × (step5_time − step3_time)` tokens — are permanently unaccounted for. They remain in the contract balance but can never be distributed to any staker.

The guard in `notifyRewardAmount` does not mitigate this:

```solidity
// Prevent starting a reward period when no tokens are staked to avoid unallocated rewards
if (totalKernelStaked == 0) revert NoStakedTokens();
```

This only blocks starting a *new* period when no one is staked. It does not prevent the mid-period drain scenario, and it does not roll over the already-lost rewards into the next period. When the admin eventually calls `notifyRewardAmount` again (after stakers have returned), the new rate is computed as:

```solidity
if (block.timestamp >= finishAt) {
    rewardRate = receivedAmount / duration;   // lost rewards are silently discarded
}
```

There is no `recoverERC20` or equivalent function in the contract, so the frozen tokens are irrecoverable.

---

### Impact Explanation

**Permanent freezing of unclaimed yield (Medium).** Reward tokens deposited by the admin for distribution to stakers become permanently locked in the contract. The exact amount frozen equals `rewardRate × T_gap`, where `T_gap` is the duration during which `totalKernelStaked == 0` within an active reward period. These tokens cannot be distributed to any staker and cannot be recovered by the admin.

---

### Likelihood Explanation

Any staker can call `initiateWithdrawal` at any time — this is an unprivileged, externally reachable action. A single whale who holds 100% of staked KERNEL can trigger this unilaterally. Even without a whale, if all stakers legitimately exit during a reward period (e.g., due to market conditions or a competing opportunity), the same loss occurs. The `notifyRewardAmount` guard demonstrates the protocol is aware of the zero-stake problem at period start, but the mid-period case is unguarded.

---

### Recommendation

Do not advance `updatedAt` when `totalKernelStaked == 0`. Modify the `updateReward` modifier so that the timestamp checkpoint is only moved forward when there are tokens staked to absorb the rewards:

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

This ensures that when `totalKernelStaked == 0`, the time gap is not consumed — the rewards for that period remain distributable once stakers return. Alternatively, add an admin `recoverERC20` function that can only withdraw tokens in excess of the currently owed reward balance.

---

### Proof of Concept

```
T=0:  Admin calls setRewardsDuration(10 days)
      Admin calls notifyRewardAmount(1000e18)
      → rewardRate = 1000e18 / 864000 ≈ 1157 tokens/sec
      → finishAt = T + 10 days, updatedAt = T

T=1d: Alice stakes 1000 KERNEL

T=2d: Alice calls initiateWithdrawal(1000)
      updateReward(Alice) runs: totalKernelStaked=1000 > 0
        rewardPerTokenStored updated correctly for day 1
        updatedAt = T+2d
        rewards[Alice] = 1 day of rewards (correctly saved)
      totalKernelStaked → 0

      [Days 2–7: no stakers, rewardRate still emitting ~1157 tokens/sec
       but no one receives them; updatedAt frozen at T+2d]

T=7d: Bob calls stake(1000)
      updateReward(Bob) runs: totalKernelStaked=0
        rewardPerToken() → rewardPerTokenStored (unchanged)
        updatedAt = lastTimeRewardApplicable() = T+7d  ← gap consumed
      totalKernelStaked → 1000

T=10d: finishAt reached
       Bob earned: rewardRate × 3 days ≈ 300e18 tokens
       Alice earned: rewardRate × 1 day ≈ 100e18 tokens
       Permanently frozen: rewardRate × 5 days ≈ 500e18 tokens
       → No function exists to recover or redistribute these 500e18 tokens
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
