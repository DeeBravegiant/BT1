Audit Report

## Title
`RSETHPriceFeed.latestRoundData()` Returns ETH/USD `updatedAt` Timestamp Instead of rsETH Oracle Update Time, Causing Incorrect Staleness Validation - (`contracts/oracles/RSETHPriceFeed.sol`)

## Summary
`RSETHPriceFeed` computes rsETH/USD by multiplying the ETH/USD Chainlink price by `LRTOracle.rsETHPrice()`, but `latestRoundData()` returns `updatedAt` sourced entirely from the ETH/USD feed while `answer` is derived from an independently-updated stored value in `LRTOracle`. Because `rsETHPrice` has no associated timestamp and is updated via a separate public call (`updateRSETHPrice()`), the returned `updatedAt` does not reflect when the rsETH component was last refreshed. Any downstream consumer performing staleness validation against `updatedAt` (e.g., Aave V3) will validate against the wrong clock.

## Finding Description
In `RSETHPriceFeed.latestRoundData()` (lines 63–70), all five return values are first populated from `ETH_TO_USD.latestRoundData()`, and then only `answer` is overwritten with the rsETH/USD composite:

```solidity
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

`updatedAt` is therefore the timestamp of the last ETH/USD Chainlink push, not the timestamp of the last `rsETHPrice` write. `rsETHPrice` is a plain `uint256` storage variable in `LRTOracle` (line 28) with no accompanying timestamp. It is written only when `_updateRsETHPrice()` is called (line 313), which is triggered by the public `updateRSETHPrice()` (line 87) or the manager-gated `updateRSETHPriceAsManager()` (line 94). These two update paths are entirely independent of Chainlink's ETH/USD heartbeat.

`getRoundData()` (lines 53–61) compounds the issue: it fetches a **historical** ETH/USD round but multiplies by the **current** `rsETHPrice`, producing a fabricated composite that is meaningless for any round other than the latest.

Existing checks are insufficient: there are no guards in `RSETHPriceFeed` that compare the age of `rsETHPrice` against any threshold, and `LRTOracle` stores no `lastUpdatedAt` field that could be used for such a check.

## Impact Explanation
**Low — Contract fails to deliver promised returns.**

`RSETHPriceFeed` is explicitly designed as a Chainlink-compatible aggregator for Aave V3 (the repository ships `IPool`, `IPoolDataProvider`, `IAToken`, and `IWrappedTokenGatewayV3` Aave interfaces). Aave V3's oracle layer validates `updatedAt` before accepting a price. Because `updatedAt` reflects the ETH/USD feed's cadence rather than the rsETH oracle's cadence, the contract systematically fails to deliver the staleness guarantee it is contractually obligated to provide as a `AggregatorV3Interface` implementation. A stale `rsETHPrice` will be presented as fresh whenever the ETH/USD feed has been updated within Aave's staleness window, and a current `rsETHPrice` may be rejected as stale if the ETH/USD feed lags. In both cases the contract does not deliver its promised return value semantics.

## Likelihood Explanation
No privileged access or attacker action is required. The divergence between the two update schedules is a normal operating condition: `updateRSETHPrice()` is a public function but must be called explicitly, while Chainlink's ETH/USD feed updates autonomously on a deviation/heartbeat basis. Any period in which the two sources fall out of sync — which is the default state between explicit `updateRSETHPrice()` calls — triggers the mismatch. Any Aave user holding rsETH as collateral is exposed on every price check.

## Recommendation
1. Add a `uint256 public rsETHPriceUpdatedAt` storage variable to `LRTOracle` and set it to `block.timestamp` inside `_updateRsETHPrice()` alongside the `rsETHPrice = newRsETHPrice` assignment (line 313).
2. Expose this timestamp via `ILRTOracle` and `IRSETHOracle`.
3. In `RSETHPriceFeed.latestRoundData()`, replace the returned `updatedAt` with `min(ethToUSD_updatedAt, rsETH_lastUpdatedAt)` so the composite price is only considered fresh when **both** components are fresh.
4. Remove or revert `getRoundData()` — it cannot return a meaningful historical rsETH/USD price without a historical rsETH/ETH rate index.

## Proof of Concept
1. `LRTOracle.rsETHPrice` was last written at `T − 25h` (no one called `updateRSETHPrice` for 25 hours).
2. The ETH/USD Chainlink feed was updated at `T − 30min` (normal heartbeat).
3. Aave calls `RSETHPriceFeed.latestRoundData()`.
4. The function returns `updatedAt = T − 30min` (from ETH/USD) and `answer` computed from the 25-hour-old `rsETHPrice`.
5. Aave's staleness guard (e.g., 1-hour window) passes: `block.timestamp − updatedAt = 30min < 1h`.
6. Aave prices rsETH collateral using a 25-hour-old rsETH/ETH rate.

Foundry fork test outline:
```solidity
// 1. Deploy or fork RSETHPriceFeed pointing at live ETH/USD feed and LRTOracle
// 2. Warp block.timestamp forward 25 hours without calling updateRSETHPrice()
// 3. Call RSETHPriceFeed.latestRoundData()
// 4. Assert: updatedAt == ETH_TO_USD.latestRoundData().updatedAt  (not T-25h)
// 5. Assert: answer uses the 25h-old rsETHPrice (unchanged in LRTOracle)
// 6. Confirm staleness window check passes despite stale rsETH component
```