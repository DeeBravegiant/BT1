### Title
Undistributed Rewards During Zero-Staker Periods Are Permanently Locked When `notifyRewardAmount` Is Called Again - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary

`KernelDepositPool.notifyRewardAmount()` computes leftover rewards as `(finishAt - block.timestamp) * rewardRate` — a purely forward-looking calculation. It does not account for rewards that accrued during any interval where `totalKernelStaked == 0`. Because `rewardPerToken()` freezes `rewardPerTokenStored` when there are no stakers while `updatedAt` still advances via the `updateReward` modifier, those rewards are silently orphaned in the contract and are never included in any future `remaining` rollover.

### Finding Description

**Root cause — `rewardPerToken()` (lines 408–413):**

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;   // ← no accumulation
    }
    return rewardPerTokenStored
        + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

When `totalKernelStaked == 0`, `rewardPerTokenStored` is not advanced. However, the `updateReward` modifier (called on every state-changing action) still executes `updatedAt = lastTimeRewardApplicable()`, moving the time pointer forward. The gap `[T_empty_start, T_empty_end]` is therefore consumed from the accounting perspective but yields nothing to stakers.

**Root cause — `notifyRewardAmount()` (lines 579–588):**

```solidity
if (block.timestamp >= finishAt) {
    rewardRate = receivedAmount / duration;
} else {
    uint256 remaining = (finishAt - block.timestamp) * rewardRate;   // ← future only
    rewardRate = (receivedAmount + remaining) / duration;
}
finishAt = block.timestamp + duration;
updatedAt = block.timestamp;
```

`remaining` captures only the rewards scheduled from `block.timestamp` to the old `finishAt`. It does not recover the rewards that were emitted at `rewardRate` during the zero-staker window and never credited to anyone. Those tokens sit in the contract balance but are invisible to the accounting.

**Step-by-step scenario:**

| Time | Event | Effect |
|------|-------|--------|
| T0 | Admin calls `notifyRewardAmount(A)` | `rewardRate = A/duration`, `finishAt = T0+duration` |
| T1 | All stakers withdraw | `totalKernelStaked = 0`; rewards T0→T1 distributed correctly |
| T1→T2 | No stakers | `rewardPerTokenStored` frozen; `updatedAt` advances on any call; `(T2-T1)*rewardRate` tokens orphaned |
| T2 | New staker deposits | `updatedAt = T2`; orphaned rewards still untracked |
| T3 | Admin calls `notifyRewardAmount(B)` | `remaining = (finishAt-T3)*rewardRate` — does **not** include `(T2-T1)*rewardRate` |

The orphaned amount `(T2 - T1) * rewardRate` remains in the contract forever; no function can recover or redistribute it.

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

Reward tokens transferred into the contract during a zero-staker window are permanently stranded. They cannot be claimed by any staker, and no admin function exists to rescue them. Every subsequent `notifyRewardAmount` call resets `finishAt` and `rewardRate` without ever touching the orphaned balance, so the loss compounds across multiple such windows.

### Likelihood Explanation

The scenario is realistic: a staking pool can naturally reach `totalKernelStaked == 0` if all depositors withdraw (e.g., during a market panic, a migration, or simply because the withdrawal delay expires for all users simultaneously). The admin then calls `notifyRewardAmount` again as part of normal operations — no malicious intent required. The `notifyRewardAmount` guard `if (totalKernelStaked == 0) revert NoStakedTokens()` only prevents starting a fresh period with no stakers; it does not prevent the mid-period drain scenario described above.

### Recommendation

Before computing `remaining`, measure the rewards that accrued while `totalKernelStaked == 0` and add them back into the new distribution. One approach: track a `stakedlessAccrued` accumulator that increments whenever `rewardPerToken()` is called with `totalKernelStaked == 0`, then include it in the `remaining` rollover inside `notifyRewardAmount`:

```solidity
uint256 remaining = (finishAt - block.timestamp) * rewardRate + stakedlessAccrued;
stakedlessAccrued = 0;
rewardRate = (receivedAmount + remaining) / duration;
```

Alternatively, mirror the CLGauge fix: always roll over the full unspent contract balance (actual `rewardsToken.balanceOf(address(this))` minus already-owed rewards) rather than relying on the rate-based projection.

### Proof of Concept

1. Admin calls `notifyRewardAmount(1_000e18)` with `duration = 7 days` → `rewardRate ≈ 1653 tokens/s`, `finishAt = now + 7 days`.
2. The single staker withdraws at `now + 1 day`. `totalKernelStaked = 0`. Rewards for day 1 are correctly credited.
3. No staker for 2 days. `(2 days * 1653) ≈ 285_696e18` tokens accrue but are never credited; `updatedAt` advances to `now + 3 days` via any external call.
4. A new staker deposits at `now + 3 days`.
5. Admin calls `notifyRewardAmount(500e18)` at `now + 3 days`:
   - `remaining = (finishAt - now) * rewardRate = 4 days * 1653 ≈ 571_392e18`
   - `rewardRate = (500e18 + 571_392e18) / 7 days`
   - The `285_696e18` orphaned tokens are **not** in `remaining` and are never redistributed.
6. At `finishAt`, the contract holds `≈ 285_696e18` tokens that no staker can ever claim. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L408-413)
```text
    function rewardPerToken() public view returns (uint256) {
        if (totalKernelStaked == 0) {
            return rewardPerTokenStored;
        }
        return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
            / totalKernelStaked;
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
