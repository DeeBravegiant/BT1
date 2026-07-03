Audit Report

## Title
`RSETHPriceFeed` Returns ETH/USD `updatedAt` Timestamp, Permanently Masking Staleness of the rsETH Price Component - (File: `contracts/oracles/RSETHPriceFeed.sol`)

## Summary
`RSETHPriceFeed.latestRoundData()` and `getRoundData()` compose a derived rsETH/USD price by multiplying the ETH/USD Chainlink answer by `LRTOracle.rsETHPrice`, but the `updatedAt` field returned is sourced entirely from the ETH/USD feed and never corrected to reflect when `rsETHPrice` was last written. Because `LRTOracle` stores no `lastUpdatedAt` field, the correct timestamp is structurally unavailable, and any integrator applying a standard Chainlink staleness check on `updatedAt` will silently accept a stale rsETH price as current.

## Finding Description
In `contracts/oracles/RSETHPriceFeed.sol`, both data-returning functions destructure all five return values from the ETH/USD feed and then overwrite only `answer`:

```solidity
// L68-69
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
// updatedAt is never overwritten — it remains ETH/USD's heartbeat timestamp
```

The same pattern appears in `getRoundData()` at L58-60. `RS_ETH_ORACLE.rsETHPrice()` reads `LRTOracle.rsETHPrice`, a plain `uint256` storage variable declared at L28 of `contracts/LRTOracle.sol`. The internal function `_updateRsETHPrice()` writes `rsETHPrice = newRsETHPrice` at L313 but records no timestamp alongside it. No `lastUpdatedAt` or equivalent field exists anywhere in `LRTOracle` or `ILRTOracle`. The ETH/USD Chainlink feed has a ~1-hour heartbeat, so `updatedAt` from that feed will always appear fresh to any staleness guard of the form `block.timestamp - updatedAt < threshold`, regardless of how long ago `rsETHPrice` was last updated. Additionally, `updateRSETHPrice()` is gated by `whenNotPaused` (L87), meaning a protocol pause simultaneously blocks price updates while the feed continues to report a fresh-looking `updatedAt`.

## Impact Explanation
The feed implements `AggregatorV3Interface` and promises to return accurate rsETH/USD price data with correct freshness metadata. It structurally cannot fulfill this promise because the `updatedAt` field is permanently sourced from the wrong component. Any integrator (such as the Morph lending protocol already integrated) that applies a staleness check on `updatedAt` will be misled into treating a potentially stale rsETH price as current. In the normal upward-drift case, the price is understated relative to reality, causing the contract to fail to deliver its promised accurate rate. This matches the allowed impact: **Low — Contract fails to deliver promised returns, but doesn't lose value**.

## Likelihood Explanation
`updateRSETHPrice()` is permissionless but depends on off-chain keepers or manual calls. Any gap between keeper runs — including maintenance windows, keeper failures, or a protocol pause triggered via `_pause()` which blocks `updateRSETHPrice` via `whenNotPaused` — leaves `rsETHPrice` stale while `updatedAt` from ETH/USD continues to appear fresh. The feed is already deployed in production and integrated with at least one external lending protocol (Morph), so the exposure is live and requires no attacker action — it is triggered by the ordinary passage of time between keeper updates.

## Recommendation
1. Add a `rsETHPriceUpdatedAt` storage variable to `LRTOracle` and set it to `block.timestamp` inside `_updateRsETHPrice()` at the point where `rsETHPrice` is written (L313).
2. Expose it via `ILRTOracle` and `IRSETHOracle`.
3. In `RSETHPriceFeed.latestRoundData()` and `getRoundData()`, override `updatedAt` with `IRSETHOracle(RS_ETH_ORACLE).rsETHPriceUpdatedAt()` instead of leaving it as the ETH/USD timestamp.
4. Consider reverting if `rsETHPriceUpdatedAt` is older than an acceptable threshold so the feed fails loudly rather than silently returning stale data.

## Proof of Concept
1. `updateRSETHPrice()` is called at time `T`. `LRTOracle.rsETHPrice` is set; no timestamp is stored.
2. 48 hours pass. The ETH/USD Chainlink feed continues to update every ~1 hour; its `updatedAt` is always within the last hour.
3. A lending protocol calls `RSETHPriceFeed.latestRoundData()`. It receives `updatedAt = block.timestamp - 30 minutes` (from ETH/USD) and `answer = rsETHPrice_at_T * ethPrice_now / 1e18`.
4. The lending protocol's staleness guard (`block.timestamp - updatedAt < 3600`) passes.
5. The rsETH price used for collateral valuation is 48 hours old.

Foundry fork test outline:
```solidity
// Fork Morph mainnet, warp 48 hours after last updateRSETHPrice call
// Call RSETHPriceFeed.latestRoundData()
// Assert: updatedAt is within last hour (from ETH/USD heartbeat)
// Assert: answer uses rsETHPrice from 48 hours ago
// Assert: block.timestamp - updatedAt < 3600 passes (staleness check fooled)
```