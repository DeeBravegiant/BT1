### Title
Rewards Permanently Locked in `KernelDepositPool` When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: `contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

`KernelDepositPool` implements a Synthetix-style staking rewards mechanism. When `totalKernelStaked` reaches zero during an active reward distribution window, the `rewardPerToken()` function freezes `rewardPerTokenStored` while the `updateReward` modifier still advances `updatedAt`. Any rewards that accrued during the zero-staker interval are permanently locked in the contract and can never be distributed to stakers.

---

### Finding Description

The `rewardPerToken()` view function short-circuits when `totalKernelStaked == 0`, returning the stored value unchanged:

```solidity
// contracts/KERNEL/KernelDepositPool.sol:408-414
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;   // ← frozen, no accumulation
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
``` [1](#0-0) 

However, the `updateReward` modifier unconditionally advances `updatedAt` to `lastTimeRewardApplicable()` on every call, regardless of whether any tokens are staked:

```solidity
// contracts/KERNEL/KernelDepositPool.sol:232-242
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();       // frozen when totalKernelStaked == 0
    updatedAt = lastTimeRewardApplicable();        // ← always advances the clock
    ...
}
``` [2](#0-1) 

The `initiateWithdrawal` function immediately decrements `totalKernelStaked` with no guard preventing it from reaching zero during an active reward period:

```solidity
// contracts/KERNEL/KernelDepositPool.sol:320-326
function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
    ...
    balanceOf[msg.sender] -= _amount;
    totalKernelStaked -= _amount;   // ← no floor check
``` [3](#0-2) 

The `notifyRewardAmount` function does include a guard against starting a reward period with zero stakers:

```solidity
// contracts/KERNEL/KernelDepositPool.sol:569-570
// Prevent starting a reward period when no tokens are staked to avoid unallocated rewards
if (totalKernelStaked == 0) revert NoStakedTokens();
``` [4](#0-3) 

But this guard only applies at the moment `notifyRewardAmount` is called. It does not prevent `totalKernelStaked` from dropping to zero **after** a reward period has started. The contract's own NatSpec acknowledges this gap but relies entirely on an off-chain operational assumption:

```
* @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
*      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
*      ensuring there are always some tokens staked...
``` [5](#0-4) 

There is no on-chain enforcement of this invariant.

---

### Impact Explanation

When `totalKernelStaked` is zero for a time interval `[T1, T2]` during an active reward period, the rewards that would have been distributed during that interval — equal to `rewardRate * (T2 - T1)` — are permanently locked in the contract. No staker can ever claim them, and there is no admin recovery function. This constitutes **permanent freezing of unclaimed yield**.

---

### Likelihood Explanation

The scenario is realistic and reachable by any staker without any privileged access:

1. Admin calls `notifyRewardAmount` while stakers are present (passes the on-chain check).
2. All stakers call `initiateWithdrawal` — a permissionless function with no floor on `totalKernelStaked`.
3. `totalKernelStaked` reaches zero mid-period.
4. Time elapses; `updateReward` is triggered by any subsequent interaction (e.g., a new staker calling `stake`), advancing `updatedAt` past the zero-staker gap.
5. Rewards for the gap are permanently lost.

This can happen organically (mass exit during market stress) or be deliberately triggered by a coordinated group of stakers. The contract's own developer comment confirms awareness of the risk.

---

### Recommendation

Two complementary mitigations:

1. **Pause `updatedAt` advancement when `totalKernelStaked == 0`** — in the `updateReward` modifier, only advance `updatedAt` when there are stakers:
   ```solidity
   modifier updateReward(address _account) {
       rewardPerTokenStored = rewardPerToken();
       if (totalKernelStaked > 0) {
           updatedAt = lastTimeRewardApplicable();
       }
       ...
   }
   ```
   This preserves the unallocated time window so rewards are distributed once stakers return.

2. **Add an admin recovery function** to sweep locked reward tokens that were never distributed, as a fallback.

---

### Proof of Concept

```
State:
  rewardRate = R (set by notifyRewardAmount at T=0, totalKernelStaked > 0)
  
T=0:   notifyRewardAmount called, finishAt = T+duration
T=10:  All stakers call initiateWithdrawal → totalKernelStaked = 0
T=20:  New staker calls stake(1e18) → updateReward fires:
         rewardPerTokenStored = rewardPerToken() → returns old rewardPerTokenStored (totalKernelStaked==0)
         updatedAt = lastTimeRewardApplicable() = T=20  ← gap [T=10, T=20] consumed
T=20+: New staker's rewardPerToken calculation uses updatedAt=T=20, skipping R*(T=20 - T=10) entirely

Locked rewards = rewardRate * 10 seconds = permanently unclaimable
```

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L18-22)
```text
 * @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
 *      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
 *      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
 *      as well as for the entire duration of the reward period. Otherwise, staking just 1 wei by any address
 *      will ensure that the contract never has any unallocated rewards.
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L232-241)
```text
    modifier updateReward(address _account) {
        rewardPerTokenStored = rewardPerToken();
        updatedAt = lastTimeRewardApplicable();

        if (_account != address(0)) {
            rewards[_account] = earned(_account);
            userRewardPerTokenPaid[_account] = rewardPerTokenStored;
        }

        _;
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
