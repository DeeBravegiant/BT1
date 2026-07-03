### Title
`RSETHPriceFeed.latestRoundData()` Returns ETH/USD `updatedAt` Timestamp Instead of rsETH Oracle Update Time, Causing Incorrect Staleness Validation - (`contracts/oracles/RSETHPriceFeed.sol`)

---

### Summary

`RSETHPriceFeed` is a Chainlink-compatible aggregator that computes rsETH/USD by combining the ETH/USD Chainlink feed with the rsETH/ETH rate from `LRTOracle`. However, `latestRoundData()` and `getRoundData()` return the `updatedAt` (and other round metadata) from the **ETH/USD feed**, while the `answer` is computed from `RS_ETH_ORACLE.rsETHPrice()`. These are two independent data sources with independent update schedules. The `updatedAt` timestamp does not reflect when the rsETH price was last updated, causing any downstream consumer (e.g., Aave) to perform staleness checks against the wrong clock.

---

### Finding Description

In `RSETHPriceFeed.sol`, both `latestRoundData()` and `getRoundData()` fetch round metadata from the ETH/USD Chainlink feed and then overwrite only the `answer` field with the current rsETH price:

```solidity
function latestRoundData()
    external view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
``` [1](#0-0) 

The `updatedAt` value returned is the timestamp of the **ETH/USD feed's last update**, not the timestamp of the last `rsETHPrice` update in `LRTOracle`. These are independent:

- `ETH_TO_USD` is a Chainlink push oracle updated by Chainlink nodes on a deviation/heartbeat schedule.
- `RS_ETH_ORACLE.rsETHPrice()` is a stored value in `LRTOracle` updated by calling `updateRSETHPrice()`. [2](#0-1) [3](#0-2) 

`getRoundData` compounds this further: it fetches **historical** ETH/USD round data but multiplies by the **current** rsETH price, producing a nonsensical composite that mixes a past ETH/USD price with the present rsETH/ETH rate:

```solidity
function getRoundData(uint80 _roundId) external view returns (...) {
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
``` [4](#0-3) 

The `RSETHPriceFeed` is designed to be consumed by Aave (the repository includes `IPool` and `IPoolDataProvider` Aave V3 interfaces): [5](#0-4) 

Aave V3 enforces a staleness check on `updatedAt` before accepting a price. Because `updatedAt` comes from the ETH/USD feed (which Chainlink updates frequently, e.g., every hour), Aave's staleness guard will pass even when `rsETHPrice` has not been updated for a much longer period.

---

### Impact Explanation

**Impact: Low → Medium (contract fails to deliver promised returns; potential temporary fund freeze or undercollateralized borrowing)**

- **Stale rsETH price accepted as fresh**: If `rsETHPrice` in `LRTOracle` has not been updated recently but the ETH/USD Chainlink feed was updated within Aave's staleness window, Aave will accept the stale rsETH price as valid. Borrowers can borrow against an outdated rsETH collateral valuation, potentially leading to undercollateralized positions and missed liquidations.
- **Fresh rsETH price rejected as stale**: If the ETH/USD Chainlink feed triggers its circuit breaker or heartbeat lapses (e.g., during low volatility), `updatedAt` will be old and Aave will reject the price feed entirely, freezing rsETH as collateral even though the rsETH oracle is current.
- **`getRoundData` returns nonsensical historical data**: Any protocol or keeper that calls `getRoundData` for historical price verification receives a fabricated composite (historical ETH/USD × current rsETH/ETH), which is incorrect for any round other than the latest.

---

### Likelihood Explanation

The `RSETHPriceFeed` is a deployed production contract intended for use as an Aave price oracle. The ETH/USD Chainlink feed and the `LRTOracle.rsETHPrice` have different update cadences by design. Any depositor using rsETH as Aave collateral is exposed to this mismatch on every price check. The scenario where ETH/USD is updated but rsETH oracle is stale is a normal operating condition (rsETH price updates require a separate `updateRSETHPrice()` call), making this a realistic and recurring condition.

---

### Recommendation

`latestRoundData()` should return the `updatedAt` timestamp that reflects when `rsETHPrice` was last written to `LRTOracle`, not the ETH/USD feed's update time. One approach:

1. Expose a `lastUpdatedAt` timestamp in `LRTOracle` that is set whenever `rsETHPrice` is updated.
2. In `RSETHPriceFeed.latestRoundData()`, use `min(ethToUSD_updatedAt, rsETH_lastUpdatedAt)` as the returned `updatedAt`, so the composite price is only considered fresh when **both** components are fresh.
3. Remove or fix `getRoundData` — it cannot return a meaningful historical rsETH/USD price without a historical rsETH/ETH rate index, so it should revert or be clearly documented as unsupported.

---

### Proof of Concept

1. `LRTOracle.rsETHPrice` is last updated at time `T - 25h` (e.g., no one called `updateRSETHPrice` for 25 hours).
2. The ETH/USD Chainlink feed was updated at `T - 30min` (normal Chainlink heartbeat).
3. Aave calls `RSETHPriceFeed.latestRoundData()`.
4. The function returns `updatedAt = T - 30min` (from ETH/USD feed) and `answer` computed from the 25-hour-old `rsETHPrice`.
5. Aave's staleness check passes (e.g., 1-hour window: `T - 30min < 1h`).
6. Aave prices rsETH collateral using a 25-hour-old rsETH/ETH rate, potentially allowing undercollateralized borrowing or preventing correct liquidations. [1](#0-0) [6](#0-5)

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

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/interfaces/aave/IPoolDataProvider.sol (L1-10)
```text
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

/**
 * @title IPoolDataProvider
 * @author Aave
 * @notice Defines the basic interface for an Aave V3 Pool Data Provider
 */
interface IPoolDataProvider {
    /**
```
