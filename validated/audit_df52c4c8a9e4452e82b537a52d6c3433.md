Audit Report

## Title
`RSETHPriceFeed` Returns ETH/USD `updatedAt` Timestamp, Permanently Masking Staleness of the rsETH Price Component - (File: `contracts/oracles/RSETHPriceFeed.sol`)

## Summary
`RSETHPriceFeed.latestRoundData()` and `getRoundData()` compose a derived rsETH/USD price by multiplying the ETH/USD Chainlink answer by `LRTOracle.rsETHPrice`, but the `updatedAt` field returned is sourced entirely from the ETH/USD Chainlink feed. Because `LRTOracle` stores no timestamp for when `rsETHPrice` was last written, the correct freshness timestamp is structurally unavailable. Any integrator applying a standard Chainlink staleness check on `updatedAt` will be misled into treating a potentially stale rsETH price as fresh.

## Finding Description
In `contracts/oracles/RSETHPriceFeed.sol`, both data-returning functions delegate all five return fields to the ETH/USD feed and only overwrite `answer`:

```solidity
// L63-70
function latestRoundData()
    external view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    // updatedAt is left as ETH_TO_USD's timestamp — never corrected
}
```

`RS_ETH_ORACLE.rsETHPrice()` reads `LRTOracle.rsETHPrice` (L28), a storage variable updated only inside `_updateRsETHPrice()` (L313). Inspection of the full `LRTOracle` contract confirms there is no `rsETHPriceUpdatedAt` or equivalent timestamp field anywhere in storage. The ETH/USD Chainlink feed has a ~1-hour heartbeat, so `updatedAt` from that feed will always appear fresh to any staleness guard of the form `block.timestamp - updatedAt < threshold`, regardless of how long ago `rsETHPrice` was last written.

`updateRSETHPrice()` is permissionless but not automatic (L87-89), and is blocked by `whenNotPaused` — meaning a protocol pause simultaneously prevents price updates while the feed continues to report a fresh `updatedAt` from ETH/USD.

## Impact Explanation
The feed implements `AggregatorV3Interface` and is deployed in production as `RSETHPriceFeed (Morph)`. It promises to return a valid rsETH/USD price with a meaningful freshness timestamp. Because `updatedAt` always reflects the ETH/USD heartbeat rather than the rsETH price update time, the feed structurally cannot deliver on this promise. This matches the allowed impact: **Low — Contract fails to deliver promised returns, but doesn't lose value** (in the normal upward-drift case).

## Likelihood Explanation
The condition is reachable by any external protocol that calls `latestRoundData()` on the deployed feed. No attacker action is required — the staleness gap opens passively whenever keeper runs are delayed, during maintenance windows, or during a protocol pause. The feed is already integrated with at least one external lending protocol (Morph), so the exposure is live and the affected callers are real integrators, not hypothetical ones.

## Recommendation
1. Add a `rsETHPriceUpdatedAt` storage variable to `LRTOracle` and set it to `block.timestamp` inside `_updateRsETHPrice()` at the point where `rsETHPrice` is written (L313).
2. Expose it via `ILRTOracle`.
3. In `RSETHPriceFeed.latestRoundData()` and `getRoundData()`, override `updatedAt` with `min(ethToUSD_updatedAt, rsETHPriceUpdatedAt)` so the returned timestamp reflects the staleness of the least-fresh component.
4. Consider reverting if `rsETHPriceUpdatedAt` exceeds an acceptable threshold (e.g., 24 hours) so the feed fails loudly rather than silently returning stale data.

## Proof of Concept
1. `updateRSETHPrice()` is called at time `T`. `LRTOracle.rsETHPrice` is set; no timestamp is stored anywhere in the contract.
2. 48 hours pass. The ETH/USD Chainlink feed continues updating every ~1 hour; its `updatedAt` is always within the last hour.
3. A lending protocol calls `RSETHPriceFeed.latestRoundData()`. It receives `updatedAt = block.timestamp - 30 minutes` (from ETH/USD) and `answer = rsETHPrice_at_T * ethPrice_now / 1e18`.
4. The lending protocol's staleness guard (`block.timestamp - updatedAt < 3600`) passes.
5. The rsETH price component used is 48 hours old. The feed has failed to deliver the promised accurate, fresh rsETH/USD rate.

**Foundry fork test plan:** Fork Morph mainnet, deploy a mock consumer that calls `RSETHPriceFeed.latestRoundData()` and asserts `block.timestamp - updatedAt < 3600`. Advance time by 48 hours without calling `updateRSETHPrice()`. Assert the staleness check still passes (demonstrating the bug) while `rsETHPrice` has not changed.