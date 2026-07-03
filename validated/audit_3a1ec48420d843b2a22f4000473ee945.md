### Title
`RSETHPriceFeed` Returns ETH/USD Staleness Metadata for a Composite rsETH/USD Price, Enabling Stale Price Consumption - (File: contracts/oracles/RSETHPriceFeed.sol)

### Summary
`RSETHPriceFeed` computes the rsETH/USD price by multiplying the rsETH/ETH rate from `RS_ETH_ORACLE` with the ETH/USD rate from `ETH_TO_USD`. However, the `updatedAt` timestamp and `answeredInRound` values returned by both `latestRoundData()` and `getRoundData()` are sourced exclusively from the ETH/USD Chainlink feed. The rsETH oracle's own update recency is never reflected in the returned metadata, breaking the invariant that staleness metadata accurately represents the freshness of the returned composite price.

### Finding Description
In `RSETHPriceFeed.latestRoundData()` and `getRoundData()`, the `answer` is computed as:

```solidity
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

This correctly combines the rsETH/ETH rate (18-decimal) with the ETH/USD Chainlink answer (8-decimal) to produce an rsETH/USD price in 8 decimals. However, the five return values `(roundId, answer, startedAt, updatedAt, answeredInRound)` are first populated entirely from `ETH_TO_USD.latestRoundData()` / `ETH_TO_USD.getRoundData()`, and only `answer` is then overwritten. The `updatedAt` and `answeredInRound` values remain those of the ETH/USD feed. [1](#0-0) 

The rsETH oracle (`LRTOracle`) has its own independent update cadence: `updateRSETHPrice()` is a public function that must be called to refresh `rsETHPrice`. [2](#0-1) 

`LRTOracle` stores no `lastUpdatedAt` timestamp, so `RSETHPriceFeed` has no mechanism to incorporate the rsETH oracle's own staleness into the returned metadata. [3](#0-2) 

The ETH/USD Chainlink feed updates every few minutes on-chain, while `rsETHPrice` is updated on a much slower cadence (typically once per day or on-demand). During any window where `updateRSETHPrice()` has not been called but the ETH/USD feed has been updated, `latestRoundData()` returns a recent `updatedAt` timestamp while serving a stale rsETH/ETH rate.

### Impact Explanation
Any external protocol (lending market, AMM, structured product) that integrates `RSETHPriceFeed` as a Chainlink-compatible rsETH/USD feed and applies a standard staleness guard (e.g., `require(block.timestamp - updatedAt < heartbeat)`) will pass the check even when the rsETH component of the price is hours or days old. The composite price returned is therefore incorrect relative to what the staleness metadata implies. This breaks the core invariant of a price feed: that `updatedAt` reflects the age of the returned price. The contract fails to deliver its promised function without directly losing value for LRT-rsETH depositors — **Low** impact.

### Likelihood Explanation
`updateRSETHPrice()` is callable by anyone when the oracle is not paused, but in practice it is called on a periodic schedule (daily or less frequently). The ETH/USD Chainlink feed heartbeat is typically 1 hour or less. Any gap between rsETH oracle updates and ETH/USD feed updates — a normal operating condition — creates a window where the staleness metadata is misleading. No special attacker action is required; simply calling `latestRoundData()` during such a window returns the incorrect metadata.

### Recommendation
Track the rsETH oracle's last update timestamp in `LRTOracle` (e.g., a `lastPriceUpdateTime` state variable set in `_updateRsETHPrice()`). In `RSETHPriceFeed.latestRoundData()` and `getRoundData()`, return `min(ethToUsdUpdatedAt, rsETHOracleLastUpdatedAt)` as `updatedAt`, and set `answeredInRound` to reflect the more stale of the two sources. This ensures consumers receive staleness metadata that accurately represents the composite price's true freshness.

### Proof of Concept
```solidity
// State: rsETH oracle last updated 25 hours ago (rsETHPrice is stale)
//        ETH/USD Chainlink feed updated 30 seconds ago

// Consumer calls RSETHPriceFeed.latestRoundData():
(, int256 answer,, uint256 updatedAt,) = rsETHPriceFeed.latestRoundData();

// updatedAt == block.timestamp - 30  (from ETH/USD feed, NOT from rsETH oracle)
// answer    == stale_rsETH_per_ETH * current_ETH_USD / 1e18  (25-hour-old rsETH rate)

// Consumer's staleness guard:
require(block.timestamp - updatedAt < 3600, "stale"); // PASSES — 30 seconds < 1 hour
// Consumer proceeds to use a 25-hour-old rsETH/USD price as if it were fresh
``` [4](#0-3) [1](#0-0) [2](#0-1) [5](#0-4)

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L53-61)
```text
    function getRoundData(uint80 _roundId)
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);

        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```

**File:** contracts/oracles/RSETHPriceFeed.sol (L63-70)
```text
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```

**File:** contracts/LRTOracle.sol (L28-29)
```text
    uint256 public override rsETHPrice;
    uint256 public pricePercentageLimit;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L313-315)
```text
        rsETHPrice = newRsETHPrice;

        emit RsETHPriceUpdate(rsETHPrice, previousPrice);
```
