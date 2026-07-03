### Title
Reward Theft via Minimal Stake When `totalKernelStaked` Drops to Zero - (File: `contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

`KernelDepositPool` uses a Synthetix-style per-token reward accumulator. When all stakers withdraw during an active reward period, `totalKernelStaked` drops to zero. An attacker can then stake 1 wei and capture **all remaining rewards** for the rest of the period, while rewards that accrued during the zero-stake window are permanently locked in the contract. The contract's own NatSpec comment acknowledges this exact risk but relies solely on an off-chain operational assumption rather than any code-level enforcement.

---

### Finding Description

The `rewardPerToken()` function short-circuits when `totalKernelStaked == 0`, returning the stored value unchanged: [1](#0-0) 

The `updateReward` modifier always advances `updatedAt` to `lastTimeRewardApplicable()`, regardless of whether `totalKernelStaked` is zero: [2](#0-1) 

**Attack path:**

1. An active reward period is running (`rewardRate > 0`, `finishAt` in the future).
2. All legitimate stakers call `initiateWithdrawal()`, which decrements `totalKernelStaked` to zero. Each call triggers `updateReward`, so `rewardPerTokenStored` and `updatedAt` are correctly snapshotted at the moment of the last withdrawal.
3. Time passes (say, 10 days). No function is called. `rewardPerTokenStored` stays frozen; `updatedAt` stays at the last-withdrawal timestamp. The 10 days of `rewardRate × 10 days` tokens are effectively orphaned in the contract.
4. Attacker calls `stake(1)`. `updateReward` fires:
   - `rewardPerTokenStored = rewardPerToken()` → `totalKernelStaked == 0`, so returns the frozen value (unchanged).
   - `updatedAt = lastTimeRewardApplicable()` → jumps forward to now, **erasing** the 10-day gap.
   - `userRewardPerTokenPaid[attacker] = rewardPerTokenStored` (the frozen value).
5. For the remaining reward period (say, another 10 days), `totalKernelStaked == 1`. Every second, `rewardPerToken()` increases by `rewardRate × Δt × 1e18 / 1`. The attacker's `earned()` accumulates the **entire** remaining reward stream.
6. Attacker calls `getReward()` and receives all remaining rewards.

The contract's own comment confirms this is a known design gap: [3](#0-2) 

The only guard against starting a new period with zero stake is in `notifyRewardAmount`: [4](#0-3) 

This check does **not** prevent all stakers from withdrawing mid-period, which is the root cause.

---

### Impact Explanation

An attacker stakes 1 wei of KERNEL and receives the entire remaining reward stream for the active period. If the period has, for example, 10 days remaining at `rewardRate = 1000 KERNEL/day`, the attacker receives 10,000 KERNEL for a cost of 1 wei. This is a direct theft of unclaimed yield from the reward pool, matching the **High – Theft of unclaimed yield** impact category.

---

### Likelihood Explanation

The trigger condition — `totalKernelStaked` reaching zero during an active reward period — is realistic:

- KERNEL stakers may exit en masse during a market downturn or if a better yield opportunity appears.
- The withdrawal mechanism (`initiateWithdrawal`) is fully permissionless and reduces `totalKernelStaked` immediately upon initiation, not upon claim.
- The protocol's only mitigation is an off-chain operational promise ("ensuring there are always some tokens staked"), with no on-chain enforcement.

An attacker monitoring the contract can detect when `totalKernelStaked` approaches zero and front-run any re-staking with a 1-wei deposit.

---

### Recommendation

1. **Code-level minimum stake**: Prevent `totalKernelStaked` from reaching zero while a reward period is active, e.g., revert `initiateWithdrawal` if it would reduce `totalKernelStaked` to zero before `finishAt`.
2. **Redirect orphaned rewards**: When `totalKernelStaked == 0` and time passes, track the orphaned reward amount and either extend the reward period or send it to the treasury, rather than leaving it claimable by the next 1-wei staker.
3. **Minimum stake threshold**: Require a meaningful minimum stake (e.g., `minStake > 0`) so that capturing the full reward stream requires a proportionally significant deposit.

---

### Proof of Concept

```
Setup:
  - rewardRate = 1000 KERNEL/day, finishAt = T0 + 30 days
  - Alice stakes 1000 KERNEL at T0

Day 10 (T0 + 10 days):
  - Alice calls initiateWithdrawal(1000 KERNEL)
  - totalKernelStaked = 0, updatedAt = T0 + 10 days

Day 20 (T0 + 20 days):
  - Attacker calls stake(1 wei)
  - updateReward: rewardPerTokenStored unchanged (totalKernelStaked was 0)
  - updatedAt jumps to T0 + 20 days (10 days of rewards locked forever)
  - userRewardPerTokenPaid[attacker] = rewardPerTokenStored

Day 30 (T0 + 30 days, end of period):
  - Attacker calls getReward()
  - earned(attacker) = 1 * (rewardRate * 10 days * 1e18 / 1) / 1e18
                     = 10,000 KERNEL
  - Attacker receives 10,000 KERNEL for 1 wei stake
  - 10,000 KERNEL from days 10–20 are permanently locked in the contract
```

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
