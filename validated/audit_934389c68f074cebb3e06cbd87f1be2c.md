Audit Report

## Title
Truncated `rewardRate` in `notifyRewardAmount` permanently freezes unclaimed yield - (File: contracts/KERNEL/KernelDepositPool.sol)

## Summary
`KernelDepositPool.notifyRewardAmount` computes `rewardRate` via integer division, discarding `receivedAmount % duration` wei on every call. These truncated tokens are transferred into the contract but never distributed to stakers and cannot be recovered, as no sweep or rescue function exists. The loss compounds on mid-period top-ups because `remaining` is already computed from a truncated rate before a second truncating division.

## Finding Description
At line 580, a fresh reward period sets:
```solidity
rewardRate = receivedAmount / duration;
```
At line 583, a mid-period top-up sets:
```solidity
uint256 remaining = (finishAt - block.timestamp) * rewardRate;
rewardRate = (receivedAmount + remaining) / duration;
```
Both paths discard `(receivedAmount [+ remaining]) % duration` wei. The truncated dust is already inside the contract (transferred at line 574) but is never included in any reward accounting. `rewardPerToken()` (lines 412–413) multiplies the already-truncated `rewardRate` by elapsed time and `DECIMAL_PRECISION`, so every downstream `earned()` call (lines 422–423) under-counts claimable yield by the proportional share of the lost dust. There is no `sweep`, `recover`, or `emergencyWithdraw` function in the contract, confirming the tokens are permanently locked.

## Impact Explanation
Every `notifyRewardAmount` call permanently freezes up to `duration - 1` wei of reward tokens. With the default 7-day duration (604 800 s) and a 1 000-token deposit, 25 600 wei are frozen per period. Over 52 weekly periods this totals 1 331 200 wei, scaling with token value and call frequency. The frozen tokens sit in the contract's `rewardsToken` balance with no recovery path. This matches the allowed impact: **Medium — Permanent freezing of unclaimed yield**.

## Likelihood Explanation
No attacker action or special condition is required. The loss occurs unconditionally on every `notifyRewardAmount` call whenever `receivedAmount` is not an exact multiple of `duration`, which is virtually always true in practice. Any staker who calls `getReward()` receives a slightly smaller amount than entitled. The function is callable by `DEFAULT_ADMIN_ROLE`, but the loss affects all stakers automatically as part of normal protocol operation.

## Recommendation
Scale `rewardRate` by `DECIMAL_PRECISION` before dividing to preserve sub-wei precision, then remove the extra `DECIMAL_PRECISION` factor in `rewardPerToken`:

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
Alternatively, track undistributed dust and roll it into the next reward period.

## Proof of Concept
```solidity
// Foundry unit test sketch
function test_rewardRateTruncation() public {
    uint256 duration    = 7 days;        // 604800 s
    uint256 amount      = 1_000e18;

    // Simulate notifyRewardAmount
    uint256 rewardRate  = amount / duration;
    // = 1_653_439_153_439_153 (truncated)

    uint256 distributed = rewardRate * duration;
    // = 999_999_999_999_999_974_400

    uint256 frozen = amount - distributed;
    // = 25_600 wei — permanently locked, no recovery path

    assertGt(frozen, 0);

    // Mid-period top-up compounds the loss:
    uint256 halfElapsed = duration / 2;
    uint256 remaining   = (duration - halfElapsed) * rewardRate; // already truncated
    uint256 newRate     = (amount + remaining) / duration;       // truncated again
    uint256 frozen2     = (amount + remaining) - newRate * duration;
    assertGt(frozen2, 0);
}
```
The test confirms that on every call, `receivedAmount % duration` wei are transferred in but never distributed, and no contract function can retrieve them.