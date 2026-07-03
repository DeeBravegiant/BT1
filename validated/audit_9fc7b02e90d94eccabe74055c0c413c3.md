Audit Report

## Title
`RSETHPriceFeed.latestRoundData()` Returns ETH/USD `updatedAt` Timestamp Instead of rsETH Oracle Update Time, Causing Incorrect Staleness Validation - (`contracts/oracles/RSETHPriceFeed.sol`)

## Summary
`RSETHPriceFeed` computes rsETH/USD by multiplying the ETH/USD Chainlink price by `LRTOracle.rsETHPrice`, but `latestRoundData()` and `getRoundData()` return all round metadata — including `updatedAt` — from the ETH/USD feed alone. Because `rsETHPrice` is a stored value updated independently via a separate `updateRSETHPrice()` call with no on-chain timestamp, the returned `updatedAt` does not reflect when the rsETH component was last refreshed. Any downstream consumer performing staleness validation against `updatedAt` (e.g., Aave V3) will operate against the wrong clock.

## Finding Description
In `RSETHPriceFeed.latestRoundData()` (lines 63–70), all five return values are populated from `ETH_TO_USD.latestRoundData()`, and only `answer` is overwritten:

```solidity
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

`RS_ETH_ORACLE.rsETHPrice()` resolves to `LRTOracle.rsETHPrice` — a plain `uint256` storage variable (line 28 of `LRTOracle.sol`) with no associated timestamp. It is written only inside `_updateRsETHPrice()` (line 313), which is triggered by the public `updateRSETHPrice()` (lines 87–89) or the manager-gated `updateRSETHPriceAsManager()`. Neither call stores a `lastUpdatedAt` timestamp anywhere.

The two data sources therefore have entirely independent update schedules:
- `ETH_TO_USD` is a Chainlink push oracle updated by Chainlink nodes on a deviation/heartbeat basis.
- `rsETHPrice` is updated only when someone explicitly calls `updateRSETHPrice()`.

The returned `updatedAt` reflects only the ETH/USD feed's last update. If `rsETHPrice` has not been refreshed for an extended period while the ETH/USD feed remains within its heartbeat, any consumer checking `updatedAt` against a staleness window will incorrectly conclude the composite price is fresh.

`getRoundData()` (lines 53–61) compounds this: it fetches a **historical** ETH/USD round but multiplies by the **current** `rsETHPrice`, producing a fabricated composite that is meaningless for any round other than the latest.

Existing checks are insufficient: there is no guard in `RSETHPriceFeed` that compares the rsETH oracle's last-write time against any threshold, because no such timestamp exists in `LRTOracle`.

## Impact Explanation
**Low — Contract fails to deliver promised returns.**

`RSETHPriceFeed` is designed as a Chainlink-compatible aggregator for Aave V3 (the repository includes `IPool` and `IPoolDataProvider` Aave V3 interfaces). The contract's implicit promise is that `updatedAt` reflects the freshness of the returned `answer`. It does not: `answer` is a composite of ETH/USD and rsETH/ETH, but `updatedAt` tracks only the ETH/USD component. A downstream consumer that relies on `updatedAt` for staleness validation will accept a stale rsETH price as fresh whenever the ETH/USD feed has been updated recently, or reject a fresh rsETH price as stale whenever the ETH/USD feed has lapsed — neither outcome matches the contract's intended behavior. No direct theft or permanent freeze is demonstrated; the concrete impact is the contract failing to deliver accurate staleness metadata as promised.

## Likelihood Explanation
The mismatch is a structural property of the contract, not a transient condition. `updateRSETHPrice()` requires a separate, explicit call; it is not atomically coupled to ETH/USD feed updates. Any period in which the ETH/USD Chainlink feed updates (its normal heartbeat) while `updateRSETHPrice()` has not been called produces the incorrect `updatedAt`. This is a normal operating condition, not an edge case.

## Recommendation
1. Store a `rsETHLastUpdatedAt` timestamp in `LRTOracle` that is set to `block.timestamp` inside `_updateRsETHPrice()` whenever `rsETHPrice` is written.
2. Expose this timestamp via `LRTOracle` (e.g., a public getter).
3. In `RSETHPriceFeed.latestRoundData()`, return `updatedAt = min(ethToUSD_updatedAt, rsETHLastUpdatedAt)` so the composite price is only considered fresh when **both** components are fresh.
4. Remove or revert `getRoundData()` — it cannot return a meaningful historical rsETH/USD price without a historical rsETH/ETH rate index.

## Proof of Concept
1. `LRTOracle.rsETHPrice` was last written at `T - 25h` (no one called `updateRSETHPrice` for 25 hours).
2. The ETH/USD Chainlink feed was updated at `T - 30min` (normal heartbeat).
3. Aave calls `RSETHPriceFeed.latestRoundData()`.
4. The function returns `updatedAt = T - 30min` (from ETH/USD) and `answer` computed from the 25-hour-old `rsETHPrice`.
5. Aave's staleness check passes (e.g., 1-hour window: `T - 30min < 1h`).
6. Aave prices rsETH collateral using a 25-hour-old rsETH/ETH rate.

Foundry fork test outline:
- Fork mainnet; deploy `RSETHPriceFeed` pointing at the live ETH/USD Chainlink feed and a mock `LRTOracle` whose `rsETHPrice` was set 25 hours ago (via `vm.warp`).
- Call `latestRoundData()`; assert `updatedAt` is within the last hour (from ETH/USD) while `answer` encodes the stale rsETH rate.
- Confirm that a staleness check of the form `require(block.timestamp - updatedAt < 3600)` passes despite the 25-hour-old rsETH price.