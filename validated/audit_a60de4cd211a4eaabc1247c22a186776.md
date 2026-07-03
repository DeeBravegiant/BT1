Audit Report

## Title
Missing Staleness Check on Cross-Chain Rate Enables Stale-Rate Deposits - (File: contracts/cross-chain/CrossChainRateReceiver.sol, contracts/pools/RSETHPoolV3.sol)

## Summary
`CrossChainRateReceiver` records `lastUpdated` on every `lzReceive()` call but `getRate()` returns `rate` unconditionally without consulting that timestamp. `RSETHPoolV3.deposit()` prices rsETH minting entirely through this unchecked rate, so any period of LayerZero relayer downtime, network congestion, or keeper inactivity causes deposits to be priced at a stale (lower) rate, over-minting rsETH relative to the true L1 exchange rate.

## Finding Description
`CrossChainRateReceiver.lzReceive()` sets `lastUpdated = block.timestamp` at line 97 each time a rate message arrives from L1. However, `CrossChainRateReceiver.getRate()` (lines 103–105) returns `rate` with no reference to `lastUpdated`:

```solidity
function getRate() external view returns (uint256) {
    return rate;
}
```

`RSETHPoolV3.getRate()` (lines 235–237) delegates directly to `IOracle(rsETHOracle).getRate()` — no staleness guard:

```solidity
function getRate() public view returns (uint256) {
    return IOracle(rsETHOracle).getRate();
}
```

`viewSwapRsETHAmountAndFee()` (lines 299–308) uses this rate to compute `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate`. A stale (lower) rate produces a larger `rsETHAmount`. `deposit()` (lines 258–262) calls `viewSwapRsETHAmountAndFee()` and mints directly from the result with no freshness check at any layer.

`MultiChainRateProvider.updateRate()` (lines 108–113) is permissionless but requires a caller to pay LayerZero fees. There is no on-chain enforcement that it is called within any time bound. Any gap — relayer downtime, fee exhaustion, network congestion, or deliberate block stuffing on the source chain — leaves `CrossChainRateReceiver.rate` frozen at the last received value while the true L1 rsETH/ETH rate continues to accrue staking yield.

## Impact Explanation
During a stale-rate window, every `deposit()` call mints rsETH using a rate lower than the true L1 rate, over-issuing rsETH to depositors. The protocol does not lose deposited principal, but it fails to deliver the correct exchange rate to all parties. This matches **Low: Contract fails to deliver promised returns, but doesn't lose value**.

## Likelihood Explanation
No attacker action is required for the passive case: LayerZero relayer downtime, fee exhaustion, or keeper inactivity all produce the same stale-rate condition at zero cost. Block stuffing on the source chain is an active but more expensive vector. The missing staleness check is a latent defect that activates whenever the rate update cadence lapses for any reason, making the condition realistically reachable by normal operational variance.

## Recommendation
Add a configurable `maxStaleness` parameter to `CrossChainRateReceiver` and revert in `getRate()` if the rate is too old:

```solidity
uint256 public maxStaleness; // e.g. 24 hours

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
```

This propagates automatically through `RSETHPoolV3.getRate()` and all callers of `viewSwapRsETHAmountAndFee()` without changes to the pool contract.

## Proof of Concept
On a local fork with `CrossChainRateReceiver` deployed as the `rsETHOracle` for `RSETHPoolV3`:

1. Record `t0 = CrossChainRateReceiver.lastUpdated()`.
2. `vm.warp(t0 + 25 hours)` — simulates relayer downtime or block stuffing.
3. Call `RSETHPoolV3.deposit{value: 1 ether}("test")`.
4. Observe: call succeeds and mints rsETH at the frozen (lower) rate rather than reverting.
5. Compare minted amount against the amount that would be minted using the current L1 rate — depositor receives more rsETH than the true exchange rate warrants.

The `lastUpdated` field is never read in the deposit flow, so no existing guard can detect or reject the stale value.