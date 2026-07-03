The vulnerability is real and reachable. Let me trace through the exact code path.

---

### Title
Permanent Freezing of Undistributed Reward Tokens When Last Staker Exits During Active Reward Period - (`contracts/KERNEL/KernelDepositPool.sol`)

### Summary
When the last staker calls `initiateWithdrawal` for their full balance during an active reward period, `totalKernelStaked` drops to zero. From that point forward, `rewardPerToken()` permanently returns the frozen `rewardPerTokenStored` value, and all reward tokens allocated for the remaining period are irrecoverably locked in the contract. There is no admin sweep or recovery function.

### Finding Description

**Step 1 — `updateReward` runs before balance reduction.**

The `initiateWithdrawal` function applies the `updateReward(msg.sender)` modifier before executing its body: [1](#0-0) 

This correctly snapshots `rewardPerTokenStored` and `earned(msg.sender)` while `totalKernelStaked` is still non-zero, so the staker's accrued rewards up to that moment are correctly credited.

**Step 2 — `totalKernelStaked` is reduced to zero.**

After the modifier, the function body decrements both `balanceOf` and `totalKernelStaked`: [2](#0-1) 

If this was the last staker, `totalKernelStaked` is now `0`.

**Step 3 — `rewardPerToken()` freezes.** [3](#0-2) 

With `totalKernelStaked == 0`, every subsequent call returns the frozen `rewardPerTokenStored`. The `rewardRate` is still non-zero and `finishAt` is still in the future, but the accumulator never advances. All reward tokens allocated for the remaining period (`rewardRate * (finishAt - block.timestamp)`) are permanently stranded.

**Step 4 — No recovery path exists.**

There is no admin function to sweep stranded reward tokens, reset the period, or rescue funds. The contract has no `emergencyWithdraw`, no `recoverERC20`, and no mechanism to restart distribution.

**Step 5 — The contract acknowledges this but relies solely on off-chain operational controls.** [4](#0-3) 

The NatSpec explicitly states the risk and says it is mitigated by "ensuring there are always some tokens staked." This is a purely operational, off-chain promise with zero on-chain enforcement. Any staker can exit at any time.

### Impact Explanation

**Impact: Medium — Permanent freezing of unclaimed yield.**

The staker correctly receives their earned rewards up to the withdrawal moment (via `getReward()`). However, the reward tokens allocated for the remainder of the period — `rewardRate * (finishAt - block.timestamp)` tokens — are permanently locked in the contract with no recovery mechanism. This is not "protocol insolvency" (the staker's principal and accrued yield are intact), but it is a permanent, irreversible loss of the admin-deposited reward tokens.

### Likelihood Explanation

- Any staker can trigger this unilaterally; no special role or collusion is required.
- The only mitigation is an off-chain operational promise, which is not enforceable.
- It can happen accidentally (a staker simply wanting to exit) or deliberately (a griefing staker).
- Likelihood is **Medium**: requires the last staker to exit mid-period, which is a plausible real-world scenario.

### Recommendation

1. Add an admin `recoverERC20` or `sweepStrandedRewards` function that can only be called after `finishAt` and only for the surplus reward balance.
2. Alternatively, enforce on-chain that `initiateWithdrawal` cannot reduce `totalKernelStaked` to zero while `block.timestamp < finishAt`, or require a minimum residual stake.
3. At minimum, add a `rescueRewards` function callable by `DEFAULT_ADMIN_ROLE` after the reward period ends to recover any unallocated reward tokens.

### Proof of Concept

```solidity
// 1. Admin sets duration and notifies reward amount (1000e18 tokens, 10-day period)
pool.setRewardsDuration(10 days);
rewardToken.approve(address(pool), 1000e18);
pool.notifyRewardAmount(1000e18);

// 2. Warp halfway through the period
vm.warp(block.timestamp + 5 days);

// 3. Last staker initiates withdrawal for full balance
// updateReward runs first: correctly credits earned rewards up to now
pool.initiateWithdrawal(stakerBalance);
// totalKernelStaked is now 0

// 4. Staker claims their correctly-accrued rewards (works fine)
pool.getReward();

// 5. Warp to end of period
vm.warp(finishAt);

// 6. Assert: ~500e18 reward tokens are permanently locked
// rewardPerToken() returns frozen rewardPerTokenStored forever
assertGt(rewardToken.balanceOf(address(pool)), 0);
// No staker can claim them; no admin can recover them
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L325-326)
```text
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
