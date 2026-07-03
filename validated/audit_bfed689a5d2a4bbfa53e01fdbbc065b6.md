### Title
Reward Tokens Permanently Locked When `totalKernelStaked` Drops to Zero During Active Reward Period - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
When all KERNEL stakers withdraw during an active reward distribution window, the remaining undistributed reward tokens become permanently locked in `KernelDepositPool` with no on-chain recovery mechanism. The contract itself acknowledges this risk in a comment but relies on a manual off-chain mitigation that is not enforced by the contract.

### Finding Description
`KernelDepositPool` implements a Synthetix-style staking rewards model. The core accounting function `rewardPerToken()` short-circuits to return the stored value unchanged whenever `totalKernelStaked == 0`:

```solidity
function rewardPerToken() public view returns (uint256) {
    if (totalKernelStaked == 0) {
        return rewardPerTokenStored;
    }
    return rewardPerTokenStored
        + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / totalKernelStaked;
}
```

When the last staker calls `initiateWithdrawal`, the `updateReward` modifier fires first, snapshotting `rewardPerTokenStored` and `updatedAt` at that moment. After the call, `totalKernelStaked` is zero. All reward tokens that would have accrued from `updatedAt` to `finishAt` — computed as `rewardRate * (finishAt - updatedAt)` — are now stranded in the contract. No admin function (`setRewardsDuration`, `notifyRewardAmount`, `setWithdrawalDelay`, `setMaxNumberOfWithdrawalsPerUser`) allows recovering these tokens.

The contract's own NatSpec comment acknowledges the root cause:

> *"If `totalKernelStaked` ever hits zero during a reward distribution window, any remaining rewards for that period will stay locked in the contract. In this deployment, we're avoiding this issue by ensuring there are always some tokens staked…"*

This is the direct analog to the reported bug: a fixed allocation (reward tokens deposited via `notifyRewardAmount`) that should be dynamically recoverable based on protocol state (zero staking), but the adjustment mechanism is entirely absent from the contract and left to manual off-chain coordination.

### Impact Explanation
Reward tokens (ERC-20 `rewardsToken`) are permanently frozen inside `KernelDepositPool` with no path to recovery. This constitutes **permanent freezing of unclaimed yield** — a Medium-severity impact per the allowed scope. The magnitude scales with the reward rate and the remaining duration: `rewardRate × (finishAt − block.timestamp)` tokens are lost.

### Likelihood Explanation
The scenario requires all stakers to withdraw during an active reward period. This is realistic when:
- The staker set is small (e.g., early deployment or low participation).
- A single large staker exits.
- Market conditions incentivize mass unstaking.

`initiateWithdrawal` is permissionless and reduces `totalKernelStaked` immediately (before the `withdrawalDelay` elapses), so the lock-in occurs the moment the last staker initiates — not when they claim.

### Recommendation
Add a permissioned `recoverLockedRewards()` function that transfers stranded reward tokens to the treasury or admin when `totalKernelStaked == 0` and the reward period has ended (or is active with zero stakers). Alternatively, track elapsed zero-staking time and exclude it from the reward window so `rewardRate` is effectively paused, resuming when staking returns.

### Proof of Concept
1. Admin calls `notifyRewardAmount(1_000e18)` while Alice is the sole staker with 100 KERNEL. `rewardRate = 1_000e18 / duration`, `finishAt = block.timestamp + duration`.
2. After half the period, Alice calls `initiateWithdrawal(100)`.
   - `updateReward(alice)` fires: `rewardPerTokenStored` and `updatedAt` are snapshotted at the current timestamp.
   - `totalKernelStaked` is decremented to 0.
3. For the remaining half-period, `rewardPerToken()` always returns `rewardPerTokenStored` (unchanged), so `rewardRate × (finishAt − updatedAt) ≈ 500e18` reward tokens accumulate in the contract but are never attributed to any staker.
4. No function in `KernelDepositPool` can extract these tokens. They are permanently locked. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L18-23)
```text
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
