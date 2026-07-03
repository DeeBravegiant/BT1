### Title
Zero-Staked Window Causes Permanent Loss of Reward Tokens During Active Distribution Period — (`contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

When the last staker calls `initiateWithdrawal`, `totalKernelStaked` drops to zero. Any `rewardRate`-denominated tokens emitted during the subsequent zero-staked window are silently skipped and permanently locked in the contract. The contract's own NatSpec acknowledges this behavior but relies on an off-chain, unenforced assumption to prevent it.

---

### Finding Description

**Root cause — `rewardPerToken()` freezes when `totalKernelStaked == 0`:**

```solidity
// lines 408-413
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;   // frozen — no accumulation
    }
    return rewardPerTokenStored
        + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
``` [1](#0-0) 

**`updateReward` still advances `updatedAt` even during the zero-staked window:**

```solidity
// lines 232-242
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();       // frozen value written back
    updatedAt = lastTimeRewardApplicable();        // timestamp advances
    ...
}
``` [2](#0-1) 

**Step-by-step exploit path:**

| Time | Action | State |
|------|--------|-------|
| T | Last staker calls `initiateWithdrawal(fullBalance)` | `totalKernelStaked = 0`, `updatedAt = T`, `rewardPerTokenStored` captures rewards up to T |
| T → T+100 | No staker present; `rewardRate` tokens continue to emit | `rewardPerToken()` returns frozen `rewardPerTokenStored`; gap = `rewardRate * 100` tokens |
| T+100 | New staker calls `stake(amount)` | `updateReward` runs **before** `totalKernelStaked` is incremented; `totalKernelStaked` is still 0 at this moment, so `rewardPerTokenStored` stays frozen; `updatedAt` jumps to T+100; `userRewardPerTokenPaid[newStaker]` = frozen value |
| T+100+ | `totalKernelStaked` becomes non-zero | Future `rewardPerToken()` accumulates from T+100 onward, skipping the T→T+100 gap entirely |

The `rewardRate * 100` tokens for the gap are in the contract's balance (deposited via `notifyRewardAmount`) but can never be claimed by anyone.

**The contract's own NatSpec acknowledges this, but the mitigation is not enforced on-chain:**

```solidity
// lines 18-22
* @dev If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards
*      for that period will stay locked in the contract. In this deployment, we're avoiding this issue by
*      ensuring there are always some tokens staked before admin calls the `notifyRewardAmount` function,
*      as well as for the entire duration of the reward period.
``` [3](#0-2) 

The `notifyRewardAmount` guard only prevents starting a period with zero stakers:

```solidity
// line 570
if (totalKernelStaked == 0) revert NoStakedTokens();
``` [4](#0-3) 

There is **no on-chain guard** preventing `totalKernelStaked` from reaching zero mid-period. Any user holding the entire staked supply can trigger this unilaterally.

---

### Impact Explanation

Reward tokens emitted during the zero-staked window are permanently locked in the contract. No staker — past, present, or future — can ever claim them. The contract fails to deliver the promised reward distribution for that interval. No principal is lost; only yield is affected.

**Scoped impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

(Also qualifies as Medium — Permanent freezing of unclaimed yield.)

---

### Likelihood Explanation

- Requires only a single staker holding 100% of `totalKernelStaked` to call `initiateWithdrawal`.
- No admin involvement, no front-running, no external dependency.
- Fully permissionless and locally reproducible on unmodified code.
- The off-chain mitigation ("ensure tokens are always staked") is fragile: it cannot be enforced if the last staker simply decides to exit.

---

### Recommendation

**Option A (preferred):** In `initiateWithdrawal`, revert if the withdrawal would set `totalKernelStaked` to zero during an active reward period:

```solidity
if (block.timestamp < finishAt && totalKernelStaked - _amount == 0) revert NoStakedTokens();
```

**Option B:** Track `rewardPerTokenStored` accumulation separately from `updatedAt` so that when `totalKernelStaked` returns to non-zero, the gap is retroactively distributed to the new staker pool (similar to Uniswap V3's per-second global accumulator approach).

**Option C (minimal):** Add an admin-callable `recoverUnallocatedRewards()` that can sweep locked gap rewards back to the treasury, preventing permanent lock-up.

---

### Proof of Concept

```solidity
// Pseudocode — locally testable with Foundry
function test_zeroStakedWindowLocksRewards() public {
    // Setup: single staker, active reward period
    pool.stake(1000e18);                          // totalKernelStaked = 1000e18
    pool.notifyRewardAmount(rewardRate * duration); // start period

    // T: last staker exits
    uint256 T = block.timestamp;
    pool.initiateWithdrawal(1000e18);             // totalKernelStaked = 0

    // T+100: new staker joins
    vm.warp(T + 100);
    pool.stake(1000e18);                          // updateReward sees totalKernelStaked==0, freezes rPTS, advances updatedAt

    // Warp to end of period
    vm.warp(finishAt);
    pool.getReward();

    uint256 totalClaimed = rewardsToken.balanceOf(address(this));
    uint256 totalEmitted = rewardRate * (finishAt - T_start);
    uint256 gap          = rewardRate * 100;

    // Assert: gap rewards are permanently locked
    assertEq(totalEmitted - totalClaimed, gap);
    assertEq(rewardsToken.balanceOf(address(pool)), gap); // stuck in contract
}
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
