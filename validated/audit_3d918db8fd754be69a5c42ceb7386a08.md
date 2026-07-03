### Title
Precision Loss in `rewardRate` Calculation Causes Permanent Freezing of Unclaimed Yield - (File: contracts/KERNEL/KernelDepositPool.sol)

### Summary

`KernelDepositPool.notifyRewardAmount()` computes `rewardRate` via integer division without any `1e18` precision scaling. This is the direct analog of the SophonFarming `pointsPerBlock` bug: a rate variable that feeds a scaled accumulator is stored truncated, causing a portion of deposited rewards to be permanently locked in the contract and, under realistic conditions, causing the per-second accumulator increment to round to zero.

### Finding Description

In `notifyRewardAmount()`, `rewardRate` is set as:

```solidity
rewardRate = receivedAmount / duration;          // line 580
// or, on renewal:
rewardRate = (receivedAmount + remaining) / duration;  // line 583
```

This is plain integer division. The remainder `receivedAmount % duration` is silently discarded and permanently locked in the contract — there is no rescue or sweep function.

`rewardRate` then feeds `rewardPerToken()`:

```solidity
return rewardPerTokenStored
    + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
    / totalKernelStaked;
```

For the accumulator increment to be non-zero for a given `timeDelta`, the contract requires:

```
rewardRate * timeDelta * 1e18 >= totalKernelStaked
```

Because `rewardRate` is stored without `1e18` scaling, this condition fails whenever `totalKernelStaked` is large relative to `rewardRate * timeDelta`. Every time `updateReward` fires (on every `stake`, `initiateWithdrawal`, or `getReward` call) and the condition fails, the rewards accrued since the last checkpoint are silently dropped.

The `rewardRate == 0` guard at line 586 only prevents the zero case; it does not prevent `rewardRate = 1` with a large staked supply.

### Impact Explanation

Two distinct losses occur:

1. **Guaranteed dust loss per period**: `receivedAmount % duration` wei are permanently locked on every call to `notifyRewardAmount`. For a 30-day duration (`2,592,000` s) with a 6-decimal reward token (e.g., USDC), up to ~2.59 USDC is lost per reward period. Over many periods this compounds.

2. **Per-checkpoint nullification**: If `rewardRate * timeDelta * 1e18 < totalKernelStaked`, the accumulator increment rounds to zero for that checkpoint. Example: `rewardRate = 1`, `timeDelta = 1 s`, `totalKernelStaked = 1e19` (10 KERNEL) → increment = `1e18 / 1e19 = 0`. Any rewards that should have accrued during that second are permanently lost because `updatedAt` is advanced regardless.

Both losses are irreversible — the reward tokens remain in the contract with no recovery path.

**Impact**: Medium — Permanent freezing of unclaimed yield.

### Likelihood Explanation

- The condition is reachable by any staker calling `stake`, `initiateWithdrawal`, or `getReward` (all trigger `updateReward`).
- No attacker action is required; normal protocol usage is sufficient.
- The scenario is realistic whenever the reward token has fewer than 18 decimals (USDC, USDT, etc.) or when the reward amount is small relative to `duration`.
- The `rewardRate == 0` revert does not prevent `rewardRate = 1` with large `totalKernelStaked`.

### Recommendation

Store `rewardRate` with `1e18` precision:

```solidity
// In notifyRewardAmount():
rewardRate = receivedAmount * DECIMAL_PRECISION / duration;
// renewal:
rewardRate = (receivedAmount * DECIMAL_PRECISION + remaining) / duration;
// where remaining = (finishAt - block.timestamp) * rewardRate  (already scaled)
```

Then remove the `* DECIMAL_PRECISION` factor from `rewardPerToken()`:

```solidity
return rewardPerTokenStored
    + (rewardRate * (lastTimeRewardApplicable() - updatedAt))
    / totalKernelStaked;
```

This mirrors the fix applied to `pointsPerBlock` in SophonFarming: enforce the precision increase at the point where the rate is stored, not downstream.

### Proof of Concept

**Setup**: `duration = 2_592_000` (30 days), reward token = USDC (6 decimals), `receivedAmount = 3_000_000` (3 USDC), `totalKernelStaked = 1e19` (10 KERNEL).

**Step 1 — dust loss**:
```
rewardRate = 3_000_000 / 2_592_000 = 1
locked forever = 3_000_000 - 1 * 2_592_000 = 408_000 wei ≈ 0.408 USDC
```

**Step 2 — per-checkpoint nullification**:
Alice calls `stake(0)` (or any function with `updateReward`) every second. Each call:
```
increment = 1 * 1 * 1e18 / 1e19 = 0
```
`rewardPerTokenStored` never increases. After the full 30-day period, Alice's `earned()` returns 0 despite 2.592 USDC having been deposited.

**Step 3 — funds locked**:
The contract holds the reward tokens but `rewardPerTokenStored` never advanced, so `getReward()` transfers 0. The 2.592 USDC (minus the 0.408 USDC dust) is permanently locked. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L579-584)
```text
        if (block.timestamp >= finishAt) {
            rewardRate = receivedAmount / duration;
        } else {
            uint256 remaining = (finishAt - block.timestamp) * rewardRate;
            rewardRate = (receivedAmount + remaining) / duration;
        }
```
