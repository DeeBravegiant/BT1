Now I have a complete picture of the `KernelDepositPool` contract. The analog is clear and direct.

### Title
Rewards permanently stuck when `totalKernelStaked` drops to zero mid-period via `initiateWithdrawal` - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool` contains the same Synthetix-style reward accounting flaw described in the reference report. When `totalKernelStaked` reaches zero during an active reward distribution window, `rewardPerToken()` returns a stale value but the `updateReward` modifier still advances `updatedAt` to the current timestamp. All reward tokens accrued during the zero-supply interval are permanently locked in the contract with no recovery path.

### Finding Description
The `rewardPerToken()` function short-circuits and returns the stored (stale) `rewardPerTokenStored` whenever `totalKernelStaked == 0`:

```solidity
// contracts/KERNEL/KernelDepositPool.sol:408-414
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;   // ← stale, no accumulation
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

However, the `updateReward` modifier unconditionally advances `updatedAt` to `lastTimeRewardApplicable()` on every invocation, regardless of whether `totalKernelStaked` is zero:

```solidity
// contracts/KERNEL/KernelDepositPool.sol:232-242
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();       // returns stale value when supply == 0
    updatedAt = lastTimeRewardApplicable();        // ← always advances the timestamp

    if (_account != address(0)) {
        rewards[_account] = earned(_account);
        userRewardPerTokenPaid[_account] = rewardPerTokenStored;
    }
    _;
}
```

The contract's own NatSpec acknowledges this design flaw and states it is mitigated operationally by ensuring tokens are always staked: [1](#0-0) 

The attempted on-chain mitigation is a guard in `notifyRewardAmount` that reverts if `totalKernelStaked == 0` at the time rewards are notified: [2](#0-1) 

This guard is **insufficient**. It only prevents starting a reward period with zero stakers. It does not prevent `totalKernelStaked` from dropping to zero **during** an already-active reward period. Any staker can call `initiateWithdrawal`, which decrements `totalKernelStaked` with no floor check: [3](#0-2) 

Once the last staker calls `initiateWithdrawal`, `totalKernelStaked` becomes 0. Every subsequent call to `updateReward` (triggered by any future `stake`, `stakeFor`, `initiateWithdrawal`, or `getReward`) will advance `updatedAt` without increasing `rewardPerTokenStored`. The reward tokens corresponding to `rewardRate × Δt` for the entire zero-supply interval are permanently unclaimable — they remain in the contract balance with no mechanism to recover them.

### Impact Explanation
**Medium — Permanent freezing of unclaimed yield.**

Reward tokens accumulate in the contract at `rewardRate` per second. For every second `totalKernelStaked == 0` during an active reward window, exactly `rewardRate` reward tokens become permanently irrecoverable. There is no admin rescue function, no sweep mechanism, and no way to re-distribute these tokens to future stakers. The contract's own comment confirms the tokens "will stay locked in the contract." [1](#0-0) 

### Likelihood Explanation
**Realistic.** The withdrawal flow is a standard user action. Any staker can call `initiateWithdrawal` for their full balance. If only one staker remains (or multiple stakers coordinate to exit), `totalKernelStaked` hits zero mid-period. The `withdrawalDelay` mechanism does not prevent this — `totalKernelStaked` is decremented immediately at `initiateWithdrawal` time, not at `claimWithdrawal` time: [4](#0-3) 

No privileged access, no oracle manipulation, and no front-running is required. A single user who is the last staker triggers this automatically.

### Recommendation
In `rewardPerToken()`, when `totalKernelStaked == 0`, do not silently skip accumulation. Instead, track the "lost" reward amount for the zero-supply interval and either:
1. Roll it forward into the next reward period when new stakers arrive (by not advancing `updatedAt` when supply is zero), or
2. Transfer it to a designated treasury/savings address.

The simplest fix is to not advance `updatedAt` in the `updateReward` modifier when `totalKernelStaked == 0`, so the time gap is preserved and rewards are correctly distributed once stakers return:

```solidity
modifier updateReward(address _account) {
    rewardPerTokenStored = rewardPerToken();
    if (totalKernelStaked > 0) {           // only advance timestamp when supply > 0
        updatedAt = lastTimeRewardApplicable();
    }
    ...
}
```

### Proof of Concept

1. Admin calls `notifyRewardAmount(1_000e18)` with `duration = 100 days` while Alice is staked. `rewardRate = 1_000e18 / (100 days)`. `finishAt = now + 100 days`. `updatedAt = now`.
2. Alice calls `initiateWithdrawal(aliceBalance)`. `totalKernelStaked` becomes 0. `updateReward` fires: `rewardPerTokenStored` stays the same (supply is 0), `updatedAt` advances to `now`.
3. 50 days pass. No stakers. `totalKernelStaked == 0` the entire time.
4. Bob calls `stake(1e18)`. `updateReward` fires again: `rewardPerToken()` returns stale `rewardPerTokenStored` (supply was 0), `updatedAt` advances to `now + 50 days`.
5. The 50 days × `rewardRate` = 500e18 reward tokens are now permanently unclaimable. Bob only earns rewards from day 50 onward, and the 500e18 tokens sit locked in the contract forever.

Key lines: [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L569-570)
```text
        // Prevent starting a reward period when no tokens are staked to avoid unallocated rewards
        if (totalKernelStaked == 0) revert NoStakedTokens();
```
