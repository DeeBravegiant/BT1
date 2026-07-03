### Title
Missing Staleness Check for `RS_ETH_ORACLE` Price in `RSETHPriceFeed.latestRoundData()` - (File: contracts/oracles/RSETHPriceFeed.sol)

---

### Summary

`RSETHPriceFeed.latestRoundData()` computes the rsETH/USD price by combining two sources: the Chainlink `ETH_TO_USD` aggregator and `RS_ETH_ORACLE.rsETHPrice()` (the stored `rsETHPrice` from `LRTOracle`). Only `ETH_TO_USD`'s round metadata (`updatedAt`, `answeredInRound`, `roundId`) is returned. The `RS_ETH_ORACLE.rsETHPrice()` component carries no staleness metadata and is never validated for freshness. Any external consumer of `RSETHPriceFeed` that checks the returned `updatedAt` for staleness will only verify the ETH/USD feed's freshness, silently accepting a potentially stale rsETH/ETH rate.

---

### Finding Description

`RSETHPriceFeed` is a Chainlink `AggregatorV3Interface`-compatible adapter designed to expose an rsETH/USD price feed to external protocols.

Its `latestRoundData()` implementation:

```solidity
// contracts/oracles/RSETHPriceFeed.sol
function latestRoundData()
    external
    view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

The final `answer` is the product of two independent price sources:
1. `ETH_TO_USD.latestRoundData()` — a live Chainlink ETH/USD feed whose freshness is reflected in the returned `updatedAt`.
2. `RS_ETH_ORACLE.rsETHPrice()` — the stored `rsETHPrice` state variable in `LRTOracle`, updated only when `updateRSETHPrice()` is called.

`LRTOracle.updateRSETHPrice()` is gated by `whenNotPaused`:

```solidity
// contracts/LRTOracle.sol
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

If the protocol is paused (or simply not updated for an extended period), `rsETHPrice` becomes stale. Yet `RSETHPriceFeed.latestRoundData()` returns `updatedAt` sourced exclusively from the live ETH/USD Chainlink feed, making the composite price appear fresh to any caller that checks `updatedAt`.

The same structural gap exists in `getRoundData()`:

```solidity
// contracts/oracles/RSETHPriceFeed.sol
function getRoundData(uint80 _roundId) external view returns (...) {
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

---

### Impact Explanation

External protocols (e.g., lending markets, collateral managers) that integrate `RSETHPriceFeed` as a Chainlink-compatible oracle will perform standard staleness checks against the returned `updatedAt`. Because `updatedAt` reflects only the ETH/USD feed's last update, a stale `rsETHPrice` passes undetected. This causes the protocol to report an incorrect rsETH/USD price as if it were fresh, which can lead to mispriced collateral, incorrect liquidation thresholds, or under-collateralized positions in any protocol consuming this feed.

Within the LRT-rsETH system itself, `ChainlinkOracleForRSETHPoolCollateral` wraps a Chainlink oracle and checks `answeredInRound < roundID` and `timestamp == 0` — if `RSETHPriceFeed` is used as its underlying oracle, these checks would only cover the ETH/USD component, not the rsETH rate.

**Impact: Low — Contract fails to deliver promised returns (fresh rsETH/USD price) without direct in-protocol fund loss; however, external integrators relying on the staleness metadata are silently misled.**

---

### Likelihood Explanation

The `rsETHPrice` stored in `LRTOracle` can become stale in two realistic scenarios:
1. The LRT-rsETH protocol is paused (e.g., due to a price deviation trigger in `_updateRsETHPrice`), blocking `updateRSETHPrice()` via the `whenNotPaused` modifier.
2. No keeper or user calls `updateRSETHPrice()` for an extended period.

In both cases, `ETH_TO_USD` continues to update normally, so the returned `updatedAt` remains fresh and no external staleness guard fires.

---

### Recommendation

Inside `RSETHPriceFeed.latestRoundData()`, track when `rsETHPrice` was last updated and expose that timestamp. The simplest approach is to have `LRTOracle` record a `rsETHPriceUpdatedAt` timestamp each time `rsETHPrice` is written, then have `RSETHPriceFeed` return `min(ethToUsdUpdatedAt, rsETHPriceUpdatedAt)` as the composite `updatedAt`. This ensures any consumer's staleness check covers both price components.

Alternatively, add an explicit staleness bound check inside `RSETHPriceFeed.latestRoundData()` that reverts if `rsETHPrice` has not been updated within an acceptable heartbeat window.

---

### Proof of Concept

**Flow:** External protocol → `RSETHPriceFeed.latestRoundData()` → stale `RS_ETH_ORACLE.rsETHPrice()` accepted as fresh

1. LRT-rsETH protocol becomes paused (e.g., price deviation auto-pause in `LRTOracle._updateRsETHPrice()`).
2. `LRTOracle.rsETHPrice` is now frozen at its last value; `updateRSETHPrice()` reverts due to `whenNotPaused`.
3. Meanwhile, the Chainlink ETH/USD feed (`ETH_TO_USD`) continues updating normally.
4. An external lending protocol calls `RSETHPriceFeed.latestRoundData()`.
5. The function returns `updatedAt` from the live ETH/USD feed — appearing fresh — while `answer` embeds the stale `rsETHPrice`.
6. The external protocol's staleness guard passes; it prices rsETH collateral using the stale rate.

Root cause lines: [1](#0-0) 

`RS_ETH_ORACLE.rsETHPrice()` is the stored state variable in `LRTOracle`: [2](#0-1) 

`updateRSETHPrice()` is blocked when paused: [3](#0-2) 

`ChainlinkOracleForRSETHPoolCollateral` performs staleness checks only on the single oracle it wraps — if that oracle is `RSETHPriceFeed`, the rsETH component is unchecked: [4](#0-3)

### Citations

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

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }
```
