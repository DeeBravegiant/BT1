Audit Report

## Title
`RSETHPriceFeed.latestRoundData()` Returns ETH/USD Staleness Metadata for a Composite rsETH/USD Price - (File: contracts/oracles/RSETHPriceFeed.sol)

## Summary
`RSETHPriceFeed` implements `AggregatorV3Interface` and computes an rsETH/USD price by multiplying the ETH/USD Chainlink price by `RS_ETH_ORACLE.rsETHPrice()`. However, the `updatedAt` and `answeredInRound` fields returned by `latestRoundData()` and `getRoundData()` are sourced exclusively from the ETH/USD Chainlink feed and carry no information about the freshness of the rsETH/ETH rate. Any integrator applying a standard Chainlink staleness guard will accept a stale rsETH/USD composite price as fresh.

## Finding Description
`RSETHPriceFeed` declares itself as an `AggregatorV3Interface` implementor at [1](#0-0)  and exposes `latestRoundData()` and `getRoundData()` to external consumers.

In `latestRoundData()`, the implementation fetches all five return values — including `updatedAt` — from the ETH/USD Chainlink feed, then overwrites only `answer` with the rsETH-adjusted price: [2](#0-1) 

The rsETH/ETH rate comes from `RS_ETH_ORACLE.rsETHPrice()`, which maps to `LRTOracle.rsETHPrice` — a plain `uint256` storage variable: [3](#0-2) 

This value is only updated when `updateRSETHPrice()` / `_updateRsETHPrice()` is explicitly called: [4](#0-3) 

`LRTOracle` stores no `lastUpdated` timestamp alongside `rsETHPrice`, and the `IRSETHOracle` interface exposed to `RSETHPriceFeed` only declares `rsETHPrice()`: [5](#0-4) 

There is therefore no mechanism by which `RSETHPriceFeed` can reflect the age of the rsETH component in the staleness fields it returns.

`getRoundData(_roundId)` has an additional defect: it fetches the ETH/USD price for a historical round but multiplies it by the *current* rsETH/ETH rate, producing a synthetic price that never existed at that round: [6](#0-5) 

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.**

`RSETHPriceFeed` advertises full `AggregatorV3Interface` compliance, including correct staleness metadata. It fails to deliver this: the `updatedAt` timestamp it returns reflects only the ETH/USD feed's freshness, not the composite price's freshness. DeFi integrators (lending markets, structured products) that apply the standard `block.timestamp - updatedAt < heartbeat` staleness guard will accept a stale rsETH/USD price as valid, potentially enabling incorrect collateral valuation. The LRT-rsETH protocol itself does not directly lose funds; the harm materializes in downstream consumers of this feed.

## Likelihood Explanation
`updateRSETHPrice()` is a keeper-driven, non-automatic call. Any gap between ETH/USD Chainlink heartbeat updates (every few minutes) and rsETH price updates (periodic, operator-triggered) creates a window where `latestRoundData()` returns a stale rsETH component with a fresh-looking `updatedAt`. This is a normal, recurring operational condition, not an edge case. No attacker action is required; the discrepancy arises passively whenever the keeper lags.

## Recommendation
`LRTOracle` should store and expose a `lastUpdated` timestamp that is set alongside `rsETHPrice` in `_updateRsETHPrice()`. `RSETHPriceFeed.latestRoundData()` should then return `min(ethToUSD_updatedAt, rsETH_lastUpdated)` as `updatedAt`:

```solidity
uint256 rsETHLastUpdated = RS_ETH_ORACLE.lastUpdated();
if (rsETHLastUpdated < updatedAt) updatedAt = rsETHLastUpdated;
```

`getRoundData` should either revert unconditionally (historical rsETH prices are not stored) or be documented as unsupported and always return current rsETH rate with a clear caveat.

## Proof of Concept
1. Deploy `RSETHPriceFeed` pointing to a live ETH/USD Chainlink feed and `LRTOracle`.
2. Call `LRTOracle.updateRSETHPrice()` to set an initial rsETH price at time T.
3. Wait 24 hours. The ETH/USD Chainlink feed continues updating every few minutes; `updateRSETHPrice()` is not called again.
4. Call `RSETHPriceFeed.latestRoundData()`.
   - `updatedAt` is recent (e.g., 5 minutes ago, from the ETH/USD feed).
   - `answer` uses the 24-hour-old rsETH price from `LRTOracle.rsETHPrice`.
5. A lending protocol checking `block.timestamp - updatedAt < 3600` passes the staleness check and accepts the stale rsETH/USD composite price as valid collateral valuation.

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L22-24)
```text
interface IRSETHOracle {
    function rsETHPrice() external view returns (uint256);
}
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L26-26)
```text
contract RSETHPriceFeed is AggregatorV3Interface {
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L58-60)
```text
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);

        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L68-69)
```text
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L313-315)
```text
        rsETHPrice = newRsETHPrice;

        emit RsETHPriceUpdate(rsETHPrice, previousPrice);
```
