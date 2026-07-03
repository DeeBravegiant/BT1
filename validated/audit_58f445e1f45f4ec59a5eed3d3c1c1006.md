Audit Report

## Title
Truncated `rewardRate` in `notifyRewardAmount` permanently freezes unclaimed yield - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`KernelDepositPool.notifyRewardAmount` computes `rewardRate` via integer division, discarding up to `duration - 1` wei of reward tokens per period. These truncated tokens are transferred into the contract but never distributed to stakers and cannot be recovered, as no sweep or recovery function exists. Every reward period permanently locks a small amount of yield that stakers are entitled to.

## Finding Description
At [1](#0-0)  the reward rate is set by integer division:

```solidity
rewardRate = receivedAmount / duration;          // line 580
// mid-period top-up:
rewardRate = (receivedAmount + remaining) / duration;  // line 583
```

Both paths truncate `receivedAmount % duration` tokens. The truncated dust is never credited to any accounting variable. `rewardRate` is then consumed in `rewardPerToken()` at [2](#0-1)  and propagated to `earned()` at [3](#0-2) , meaning every downstream reward calculation uses the under-counted rate. A grep search of the contract confirms no `recover`, `sweep`, or `rescue` function exists, so the truncated tokens sit permanently in the `rewardsToken` balance with no path to extraction.

## Impact Explanation
**Medium — Permanent freezing of unclaimed yield.** Reward tokens are transferred into the contract via `safeTransferFrom` at [4](#0-3) , but up to `duration - 1` wei per period are never distributed. For the default 7-day duration (604,800 seconds), up to 604,799 wei are frozen per `notifyRewardAmount` call. Mid-period top-ups compound the loss because `remaining` is already computed from a truncated `rewardRate` before the second division. The frozen tokens are irrecoverable.

## Likelihood Explanation
This triggers unconditionally on every `notifyRewardAmount` call whenever `receivedAmount` is not an exact multiple of `duration`. No attacker action is required — the loss is an automatic consequence of normal protocol operation. Any staker calling `getReward()` receives a marginally smaller amount than they are entitled to. The `onlyRole(DEFAULT_ADMIN_ROLE)` gate on `notifyRewardAmount` is the intended operational path, not a privilege-abuse vector; the truncation is a math property of the function itself.

## Recommendation
Scale `rewardRate` by `DECIMAL_PRECISION` before dividing to preserve sub-wei precision in the rate, then remove the extra `DECIMAL_PRECISION` factor from `rewardPerToken`:

```diff
- rewardRate = receivedAmount / duration;
+ rewardRate = (receivedAmount * DECIMAL_PRECISION) / duration;
```

```diff
- return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt) * DECIMAL_PRECISION)
-     / totalKernelStaked;
+ return rewardPerTokenStored + (rewardRate * (lastTimeRewardApplicable() - updatedAt))
+     / totalKernelStaked;
```

Alternatively, track the undistributed remainder (`receivedAmount % duration`) and roll it into the next reward period.

## Proof of Concept
```solidity
// Foundry unit test sketch
function test_truncatedRewardRate() public {
    uint256 dur = 7 days;          // 604_800 seconds
    uint256 amount = 1_000e18;     // 1000 reward tokens

    // Simulate notifyRewardAmount
    uint256 rate = amount / dur;
    // rate = 1_653_439_153_439_153

    uint256 totalDistributed = rate * dur;
    // = 999_999_999_999_999_974_400

    uint256 frozen = amount - totalDistributed;
    // = 25_600 wei — permanently locked, no recovery path

    assertGt(frozen, 0);

    // Mid-period top-up: remaining uses already-truncated rate
    uint256 halfPeriod = dur / 2;
    uint256 remaining = halfPeriod * rate;
    uint256 newRate = (amount + remaining) / dur;
    // Second truncation compounds the loss
    uint256 frozen2 = (amount + remaining) - (newRate * dur);
    assertGt(frozen2, 0);
}
```

Over 52 weekly periods: `52 × 25,600 = 1,331,200 wei` permanently frozen. With higher-value tokens or shorter durations the absolute loss scales proportionally. The absence of any recovery function (confirmed by code search) makes the freeze permanent.

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

**File:** contracts/KERNEL/KernelDepositPool.sol (L574-577)
```text
        rewardsToken.safeTransferFrom(msg.sender, address(this), _amount);
        uint256 balanceAfter = rewardsToken.balanceOf(address(this));
        // Calculate the actual amount of tokens received in case of a transfer fee (tax)
        uint256 receivedAmount = balanceAfter - balanceBefore;
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
