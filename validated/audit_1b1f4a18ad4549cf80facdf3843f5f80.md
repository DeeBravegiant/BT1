### Title
Permanent Freezing of Reward Tokens When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: `contracts/KERNEL/KernelDepositPool.sol`)

---

### Summary

`KernelDepositPool` implements a Synthetix-style staking rewards mechanism. When all stakers withdraw their KERNEL tokens during an active reward distribution window, `totalKernelStaked` drops to zero. The `rewardPerToken()` function stops advancing when `totalKernelStaked == 0`, causing all remaining rewards for that period to be permanently trapped in the contract. No admin recovery function exists.

---

### Finding Description

The `rewardPerToken()` function in `KernelDepositPool` has a zero-staker guard:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;
    }
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

When `totalKernelStaked == 0`, the function returns the stored value unchanged. Any rewards that should have accrued during the zero-staker window are never allocated to any account and are permanently irrecoverable.

The `initiateWithdrawal` function is callable by any staker with no restriction on reducing `totalKernelStaked` to zero during an active reward period:

```solidity
function initiateWithdrawal(uint256 _amount) external nonReentrant updateReward(msg.sender) {
    ...
    balanceOf[msg.sender] -= _amount;
    totalKernelStaked -= _amount;
    ...
}
```

The contract's own NatSpec explicitly acknowledges this risk:

> *"If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards for that period will stay locked in the contract."*

The guard in `notifyRewardAmount` only prevents starting a new reward period with zero stakers:

```solidity
if (totalKernelStaked == 0) revert NoStakedTokens();
```

It does **not** prevent `totalKernelStaked` from reaching zero after a reward period has already started. There is no function in the contract to sweep or recover stranded reward tokens. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

**Medium — Permanent freezing of unclaimed yield.**

Reward tokens transferred into the contract via `notifyRewardAmount` become permanently inaccessible if `totalKernelStaked` reaches zero at any point during the active reward window. The contract holds the tokens but has no mechanism to redistribute or recover them. This directly maps to the analog vulnerability class: funds are escrowed (reward tokens deposited) but a specific execution path (all stakers withdraw mid-period) causes them to be permanently trapped with no recovery path.

---

### Likelihood Explanation

**Medium.**

Any staker can unilaterally call `initiateWithdrawal` for their full balance. If a single dominant staker (or the last remaining staker) exits during an active reward period, `totalKernelStaked` drops to zero and the remaining rewards are frozen. This requires no admin compromise, no front-running, and no external dependency — it is reachable by any ordinary staker through the standard withdrawal path. The contract itself acknowledges the scenario is possible and relies entirely on off-chain operational discipline (always keeping at least 1 wei staked), which is not enforced at the code level.

---

### Recommendation

**Short term:** Add a `recoverUnallocatedRewards()` admin function that can sweep reward tokens that exceed the amount owed to current stakers, allowing recovery when `totalKernelStaked` is zero.

**Long term:** Enforce at the code level that `totalKernelStaked` cannot reach zero while a reward period is active (i.e., `block.timestamp < finishAt`), or track unallocated rewards and roll them into the next reward period automatically via `notifyRewardAmount`.

---

### Proof of Concept

1. Admin calls `notifyRewardAmount(1_000e18)` with `totalKernelStaked > 0`. This sets `rewardRate` and `finishAt = block.timestamp + duration`.
2. The sole (or last remaining) staker calls `initiateWithdrawal(totalStake)`. `totalKernelStaked` drops to `0`. The `updateReward(msg.sender)` modifier snapshots their earned rewards correctly up to this point.
3. Time advances; `block.timestamp < finishAt` (reward period still active). `rewardPerToken()` returns `rewardPerTokenStored` unchanged because `totalKernelStaked == 0`. Rewards continue to be emitted at `rewardRate` per second but are allocated to no one.
4. `finishAt` passes. The unallocated rewards — `rewardRate * (finishAt - withdrawalTimestamp)` tokens — remain in the contract balance.
5. No function exists to recover these tokens. They are permanently frozen. [2](#0-1) [5](#0-4) [6](#0-5)

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
