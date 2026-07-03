### Title
`RSETHPriceFeed.getRoundData` Returns Corrupted Historical Prices by Mixing Historical ETH/USD with Current rsETH Rate - (File: contracts/oracles/RSETHPriceFeed.sol)

---

### Summary

`RSETHPriceFeed` implements `AggregatorV3Interface` and is deployed as a Chainlink-compatible RSETH/USD price feed for consumption by external protocols. Its `getRoundData` function fetches the historical ETH/USD price for a given `_roundId` but always multiplies by the **current** rsETH/ETH rate from `RS_ETH_ORACLE.rsETHPrice()`. This produces a corrupted answer that is neither the historical RSETH/USD price nor the current one — it is a meaningless hybrid.

---

### Finding Description

`RSETHPriceFeed.getRoundData` is defined as:

```solidity
function getRoundData(uint80 _roundId)
    external view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
``` [1](#0-0) 

The `ETH_TO_USD.getRoundData(_roundId)` call correctly retrieves the historical ETH/USD price for the requested round. However, `RS_ETH_ORACLE.rsETHPrice()` always returns the **current** rsETH/ETH exchange rate — there is no mechanism to retrieve the historical rsETH/ETH rate for that same round. [2](#0-1) 

The result is:

```
returned_answer = current_rsETH_per_ETH × historical_ETH_USD_price
```

instead of the correct:

```
correct_answer = historical_rsETH_per_ETH × historical_ETH_USD_price
```

The `latestRoundData` path is unaffected because both components are current there. [3](#0-2) 

---

### Impact Explanation

`RSETHPriceFeed` is a production contract that exposes the full `AggregatorV3Interface`, including `getRoundData`, specifically so that external DeFi protocols (e.g., Aave, Compound, Curve, or any protocol that performs historical round deviation checks similar to SteadeFi's `_badPriceDeviation`) can consume RSETH/USD prices. Any such protocol that calls `getRoundData` to validate that the latest price has not deviated excessively from a recent historical round will receive a fabricated answer. Depending on the direction of rsETH price drift since the queried round, the returned historical price will be either inflated or deflated relative to the true historical value. This can cause:

- Incorrect staleness/deviation checks to pass or fail, leading to **incorrect liquidations of rsETH collateral positions** held by rsETH holders.
- Protocols that gate actions on historical price consistency to be permanently or temporarily blocked, **freezing rsETH-backed positions**.

Impact: **Medium — Temporary freezing of funds / incorrect liquidation of rsETH holders** (rsETH holders are explicitly in scope).

---

### Likelihood Explanation

`RSETHPriceFeed` is deployed precisely to be registered as a Chainlink-compatible feed in external lending/derivatives protocols. Any integrating protocol that performs historical round validation (a standard Chainlink best-practice pattern) will trigger this path. The function is permissionlessly callable by any external contract. The rsETH/ETH rate changes over time as staking rewards accrue, so the discrepancy between current and historical rsETH price grows continuously, making the corrupted answer increasingly wrong over time.

---

### Recommendation

`getRoundData` cannot be correctly implemented without a historical rsETH/ETH rate oracle. The options are:

1. **Remove `getRoundData`** and revert with an explicit `NotSupported()` error, so integrating protocols cannot silently consume incorrect data.
2. **Store historical rsETH prices on-chain** (e.g., keyed by ETH/USD roundId) and use the stored value in `getRoundData`.
3. **Document and enforce** that `RSETHPriceFeed` must only be registered in protocols that exclusively use `latestRoundData` and never `getRoundData`.

---

### Proof of Concept

1. At time T0, rsETH/ETH = 1.05, ETH/USD = 2000. True RSETH/USD at round R = 2100.
2. At time T1, rsETH/ETH = 1.10, ETH/USD = 1800 (latest round R+N).
3. An external protocol calls `RSETHPriceFeed.getRoundData(R)` to check historical deviation.
4. The call returns: `1.10 × 2000 = 2200` instead of the correct `1.05 × 2000 = 2100`.
5. The protocol computes deviation between current price (1.10 × 1800 = 1980) and "historical" price (2200): a 10.1% drop — potentially triggering a false circuit-breaker or false staleness revert, freezing rsETH-collateralized positions. [1](#0-0)

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
