### Title
Premature `totalKernelStaked` Decrement in `initiateWithdrawal` Inflates Rewards for Remaining Stakers at Withdrawing Users' Expense - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
In `KernelDepositPool`, calling `initiateWithdrawal` immediately decrements both `balanceOf[msg.sender]` and `totalKernelStaked` even though the KERNEL tokens remain locked in the contract for up to 30 days. Because `rewardPerToken()` uses `totalKernelStaked` as its denominator, the withdrawing user stops earning rewards the instant they initiate a withdrawal, while remaining stakers receive an inflated reward rate for the entire lock period.

### Finding Description
`initiateWithdrawal` reduces the staking accounting variables immediately upon call:

```solidity
// contracts/KERNEL/KernelDepositPool.sol lines 325-326
balanceOf[msg.sender] -= _amount;
totalKernelStaked -= _amount;
```

The tokens are not transferred out — they remain in the contract until `claimWithdrawal` is called after `withdrawalDelay` (up to `MAX_WITHDRAWAL_DELAY = 30 days`). However, `rewardPerToken()` divides by the now-reduced `totalKernelStaked`:

```solidity
// contracts/KERNEL/KernelDepositPool.sol lines 412-413
return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / totalKernelStaked;
```

And `earned()` multiplies by the now-zeroed `balanceOf[_account]`:

```solidity
// contracts/KERNEL/KernelDepositPool.sol line 422
return (balanceOf[_account] * (rewardPerToken() - userRewardPerTokenPaid[_account]) / DECIMAL_PRECISION)
    + rewards[_account];
```

The result is that the withdrawing user's tokens — still physically locked in the contract, inaccessible to the user — are excluded from the reward denominator. The fixed `rewardRate` is now divided among a smaller `totalKernelStaked`, so every remaining staker's `rewardPerToken` is inflated for the entire withdrawal delay window.

### Impact Explanation
**High — Theft of unclaimed yield.**

A user who calls `initiateWithdrawal` forfeits all rewards accruing during the mandatory lock period (up to 30 days). Those rewards are not burned; they are redistributed to remaining stakers. The withdrawing user's tokens are economically captive — they cannot be restaked, transferred, or used elsewhere — yet they earn nothing. The magnitude scales with: `rewardRate × withdrawalDelay × (withdrawnAmount / totalKernelStaked_before)`. With a 30-day delay and a meaningful stake, this is a material loss of yield.

### Likelihood Explanation
**High.** Every user who calls `initiateWithdrawal` is affected unconditionally. No special conditions, no front-running, no admin involvement. The withdrawal delay is a mandatory protocol parameter (up to 30 days), making the yield loss window large. This is a normal, expected user action on the critical path of the protocol.

### Recommendation
Track tokens in the pending-withdrawal state separately (e.g., `totalPendingWithdrawal`) and include them in the reward denominator:

```solidity
function rewardPerToken() public view returns (uint256) {
    uint256 effectiveStake = totalKernelStaked + totalPendingWithdrawal;
    if (effectiveStake == 0) return rewardPerTokenStored;
    return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
        / effectiveStake;
}
```

Correspondingly, maintain a per-user `pendingWithdrawalBalance` and include it in `earned()`. Decrement `totalPendingWithdrawal` only when `claimWithdrawal` actually transfers tokens out.

### Proof of Concept

**Setup:** Alice and Bob each stake 1000 KERNEL. `totalKernelStaked = 2000`. Admin calls `notifyRewardAmount` with `rewardRate = 1 token/second` and `duration = 30 days`.

**Step 1:** Alice calls `initiateWithdrawal(1000)`.
- `balanceOf[Alice] = 0`, `totalKernelStaked = 1000` (immediately).
- Alice's tokens remain locked for 30 days.

**Step 2:** For the next 30 days, `rewardPerToken()` computes:
```
rewardRate * elapsed * DECIMAL_PRECISION / totalKernelStaked
= 1 * 2592000 * 1e18 / 1000   // Bob gets ALL rewards
```

**Step 3:** Bob calls `getReward()` and receives the full 2,592,000 reward tokens.

**Step 4:** Alice calls `claimWithdrawal` and receives only her 1000 KERNEL principal — zero rewards for 30 days of locked capital.

**Expected:** Alice should have earned ~50% of rewards (≈1,296,000 tokens) during the lock period since her tokens were still locked and unavailable. Instead, Bob received 100% of rewards. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/KERNEL/KernelDepositPool.sol (L34-35)
```text
    /// @notice The maximum withdrawal delay allowed
    uint256 public constant MAX_WITHDRAWAL_DELAY = 30 days;
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

**File:** contracts/KERNEL/KernelDepositPool.sol (L421-423)
```text
    function earned(address _account) public view returns (uint256) {
        return (balanceOf[_account] * (rewardPerToken() - userRewardPerTokenPaid[_account]) / DECIMAL_PRECISION)
            + rewards[_account];
```
