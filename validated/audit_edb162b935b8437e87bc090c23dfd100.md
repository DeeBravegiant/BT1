### Title
Divide-Before-Multiply in `notifyRewardAmount` Permanently Freezes Reward Tokens - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary
`KernelDepositPool.notifyRewardAmount` computes `rewardRate` via integer division before that rate is used in subsequent multiplications. The truncated remainder (`receivedAmount % duration`) is transferred into the contract but can never be distributed, permanently freezing a portion of reward tokens each period.

### Finding Description
In `notifyRewardAmount`, the reward rate is computed as:

```solidity
rewardRate = receivedAmount / duration;          // line 580
// or, when a period is still active:
rewardRate = (receivedAmount + remaining) / duration;  // line 583
```

Solidity integer division truncates the result. The discarded remainder — `receivedAmount % duration` tokens — is already held by the contract (transferred in at lines 573–577) but will never be emitted to stakers, because `rewardPerToken()` only ever distributes `rewardRate * duration` tokens over the full period:

```solidity
return rewardPerTokenStored
    + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / totalKernelStaked;
```

The maximum loss per period is `duration − 1` wei of the reward token. For a 30-day duration (`2_592_000` seconds) this is up to `2_591_999` wei per `notifyRewardAmount` call. These tokens accumulate silently in the contract across every reward period and are irrecoverable.

### Impact Explanation
Every call to `notifyRewardAmount` permanently freezes up to `duration − 1` wei of reward tokens. The tokens are in the contract but no code path can distribute them. This matches **"Permanent freezing of unclaimed yield"** (Medium) or **"Contract fails to deliver promised returns"** (Low). For 18-decimal tokens the per-period loss is ~2.6 × 10⁻¹² tokens — negligible in isolation — but it is deterministic, cumulative across all periods, and irrecoverable.

### Likelihood Explanation
Triggered unconditionally on every legitimate admin call to `notifyRewardAmount`. No attacker action is required; normal protocol operation is sufficient.

### Recommendation
Accumulate the undistributed remainder and roll it into the next period, or refund it to the caller:

```solidity
// Option A – roll remainder forward
uint256 leftover = receivedAmount % duration;
rewardRate = (receivedAmount - leftover) / duration;
// carry leftover into next notifyRewardAmount call

// Option B – compute rate first, then verify no tokens are stranded
rewardRate = receivedAmount / duration;
uint256 distributed = rewardRate * duration;
if (distributed < receivedAmount) {
    rewardsToken.safeTransfer(msg.sender, receivedAmount - distributed);
}
```

### Proof of Concept

1. Admin calls `notifyRewardAmount(1_000_000_007)` with `duration = 7 days` (604 800 s).
2. `rewardRate = 1_000_000_007 / 604_800 = 1653` (truncated).
3. Total distributed over the period: `1653 × 604_800 = 999_734_400`.
4. Permanently frozen: `1_000_000_007 − 999_734_400 = 265_607` wei of reward token.
5. These tokens sit in the contract forever; no function can recover or distribute them. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L421-424)
```text
    function earned(address _account) public view returns (uint256) {
        return (balanceOf[_account] * (rewardPerToken() - userRewardPerTokenPaid[_account]) / DECIMAL_PRECISION)
            + rewards[_account];
    }
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L566-591)
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
```
