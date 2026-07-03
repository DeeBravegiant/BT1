Audit Report

## Title
Stale Rate in `CrossChainRateReceiver.getRate()` Enables Over-Minting of wrsETH — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary

`CrossChainRateReceiver` stores both `rate` and `lastUpdated` on every `lzReceive` call, but `getRate()` returns `rate` unconditionally with no staleness check. Both `RSETHPoolV2` and `RSETHPoolV3` call this oracle directly during `deposit` to compute the wrsETH mint amount. If the LayerZero rate-update pipeline stalls, the stale (lower) rate causes every depositor to receive more wrsETH than their ETH can back at the true current rate, breaking the backing invariant and causing protocol insolvency.

## Finding Description

`CrossChainRateReceiver` stores the rate and its timestamp on receipt:

```solidity
// CrossChainRateReceiver.sol L13-16
uint256 public rate;
uint256 public lastUpdated;
```

`lzReceive` updates both fields when a LZ message arrives (L95-97), but `getRate()` ignores `lastUpdated` entirely:

```solidity
// CrossChainRateReceiver.sol L102-105
function getRate() external view returns (uint256) {
    return rate;
}
```

Both pool contracts delegate their oracle call to this function without any freshness guard:

- `RSETHPoolV2.getRate()` (L200-203) → `IOracle(rsETHOracle).getRate()`
- `RSETHPoolV3.getRate()` (L234-237) → `IOracle(rsETHOracle).getRate()`

The minting math in both pools divides by the oracle rate directly:

```solidity
// RSETHPoolV2.sol L225-233 / RSETHPoolV3.sol L299-307
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

If `rate == 0` (before the first LZ message is ever delivered), this division reverts, freezing all deposits until a valid LZ message arrives.

The `dailyMintLimit` does not mitigate the over-minting ratio: the limit is denominated in rsETH, so a stale (lower) rate causes the limit to be consumed faster per ETH deposited, not slower.

## Impact Explanation

rsETH is a liquid staking token whose ETH-denominated rate monotonically increases over time. A stale rate is always lower than the true current rate. Because `rsETHAmount = amountAfterFee * 1e18 / staleRate`, a lower denominator produces a larger wrsETH mint. Every depositor during the stale window receives more wrsETH than the deposited ETH can back at the true rate. This directly causes **protocol insolvency** (Critical). The zero-rate path causes a **temporary freezing of funds** (Medium/Critical) for all depositors until a valid LZ message is received.

## Likelihood Explanation

`CrossChainRateProvider.updateRate()` is permissionless but requires the caller to supply ETH for LZ fees (L85-90). In practice, a protocol-operated keeper funds these updates. If the keeper stops (infrastructure failure, key loss, budget exhaustion), the receiver's `rate` silently ages. There is no on-chain circuit-breaker, no heartbeat requirement, and no admin alert. Any depositor calling `deposit()` during the stale window triggers the over-minting — no special privileges or attacker coordination required. This is a realistic operational failure mode.

## Recommendation

Add a `MAX_STALENESS` constant and enforce it in `getRate()`:

```solidity
// CrossChainRateReceiver.sol
uint256 public constant MAX_STALENESS = 24 hours;

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= MAX_STALENESS, "Rate is stale");
    return rate;
}
```

Alternatively, enforce the check at the pool level inside `viewSwapRsETHAmountAndFee`, or expose `lastUpdated` through the `IOracle` interface so pools can validate freshness before minting.

## Proof of Concept

```solidity
// Local fork test (no mainnet):
// 1. Deploy CrossChainRateReceiver (RSETHRateReceiver) with a mock LZ endpoint.
// 2. Simulate one lzReceive call: rate = 1.05e18, lastUpdated = block.timestamp.
// 3. vm.warp(block.timestamp + 30 days).
// 4. Call getRate() → returns 1.05e18 (stale, no revert).
// 5. Deploy RSETHPoolV2 pointing to the receiver as rsETHOracle.
// 6. Call pool.deposit{value: 1 ether}("ref").
//    → rsETHAmount = 1e18 * 1e18 / 1.05e18 ≈ 0.952e18  (stale, inflated)
// 7. Simulate fresh lzReceive: rate = 1.10e18 (true current rate).
// 8. Call pool.deposit{value: 1 ether}("ref").
//    → rsETHAmount = 1e18 * 1e18 / 1.10e18 ≈ 0.909e18  (correct)
// 9. assertGt(step6Amount, step8Amount);
//    Difference ≈ 0.043e18 wrsETH per ETH is unbacked → protocol insolvency.
//
// Zero-rate path:
// 10. Deploy fresh receiver (rate == 0, no lzReceive yet).
// 11. Call pool.deposit{value: 1 ether} → reverts (division by zero).
//     All deposits frozen until a valid LZ message arrives.
```