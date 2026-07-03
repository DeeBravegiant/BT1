### Title
`RSETHPriceFeed` Advertises but Does Not Honor the Chainlink `AggregatorV3Interface` Staleness Standard - (File: contracts/oracles/RSETHPriceFeed.sol)

---

### Summary

`RSETHPriceFeed` implements `AggregatorV3Interface` to serve as a Chainlink-compatible rsETH/USD price feed for external DeFi integrators. However, the `updatedAt` and `answeredInRound` fields returned by both `latestRoundData()` and `getRoundData()` reflect only the ETH/USD Chainlink feed's freshness — not the freshness of the rsETH price component sourced from `RS_ETH_ORACLE`. This means any consumer performing standard Chainlink staleness checks will be misled into treating a stale rsETH price as fresh.

---

### Finding Description

`RSETHPriceFeed` declares itself as an `AggregatorV3Interface` implementor:

```solidity
contract RSETHPriceFeed is AggregatorV3Interface {
``` [1](#0-0) 

Both `latestRoundData()` and `getRoundData()` compute the final `answer` by multiplying the ETH/USD Chainlink price by the current rsETH/ETH rate from `RS_ETH_ORACLE`:

```solidity
function latestRoundData() external view returns (...) {
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
``` [2](#0-1) 

The `updatedAt` and `answeredInRound` values are passed through directly from the ETH/USD Chainlink feed. They carry no information about when `RS_ETH_ORACLE.rsETHPrice()` was last updated.

The rsETH price is stored in `LRTOracle.rsETHPrice` and is only updated when `updateRSETHPrice()` is explicitly called:

```solidity
rsETHPrice = newRsETHPrice;
emit RsETHPriceUpdate(rsETHPrice, previousPrice);
``` [3](#0-2) 

If `updateRSETHPrice()` has not been called for an extended period, `RS_ETH_ORACLE.rsETHPrice()` returns a stale value. Yet `RSETHPriceFeed.latestRoundData()` will still return the ETH/USD feed's recent `updatedAt` timestamp (e.g., updated 5 minutes ago), making the composite rsETH/USD price appear fresh to any consumer performing a standard Chainlink staleness check (`block.timestamp - updatedAt < heartbeat`).

Additionally, `getRoundData(_roundId)` fetches the ETH/USD price for a historical round but multiplies it by the *current* rsETH/ETH rate, producing a synthetic price that never existed at that round. This violates the `AggregatorV3Interface` contract, which requires `getRoundData` to return the price as it was at that specific round. [4](#0-3) 

---

### Impact Explanation

DeFi protocols (lending markets, derivatives, structured products) that integrate `RSETHPriceFeed` as a Chainlink-compatible oracle and apply standard staleness guards will be misled. When the rsETH oracle price is stale, the composite rsETH/USD price returned is incorrect, but the `updatedAt` timestamp signals freshness. This causes integrators to use a stale rsETH/USD price for collateral valuation, potentially enabling over-borrowing against inflated rsETH collateral or triggering incorrect liquidations.

**Impact: Low** — Contract fails to deliver promised returns (the staleness metadata is incorrect), but the LRT-rsETH protocol itself does not directly lose value. The harm materializes in downstream integrators.

---

### Likelihood Explanation

The rsETH price update is not automatic; it requires an explicit call to `LRTOracle.updateRSETHPrice()`. Any gap between ETH/USD feed updates (frequent, every few minutes) and rsETH price updates (periodic, keeper-driven) creates a window where `RSETHPriceFeed` returns a stale rsETH component with a fresh-looking `updatedAt`. This is a realistic and recurring condition in normal protocol operation.

---

### Recommendation

`RSETHPriceFeed.latestRoundData()` should return the *minimum* of the ETH/USD `updatedAt` and the timestamp at which the rsETH oracle price was last updated. This requires `LRTOracle` to expose a `lastUpdated` timestamp alongside `rsETHPrice()`, and `RSETHPriceFeed` to use it:

```solidity
function latestRoundData() external view returns (...) {
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    // Use the older of the two timestamps so staleness is correctly signaled
    uint256 rsETHLastUpdated = RS_ETH_ORACLE.lastUpdated();
    if (rsETHLastUpdated < updatedAt) updatedAt = rsETHLastUpdated;
}
```

`getRoundData` should either revert (since historical rsETH prices are not stored) or be clearly documented as unsupported.

---

### Proof of Concept

1. Deploy `RSETHPriceFeed` pointing to a live ETH/USD Chainlink feed and `LRTOracle`.
2. Call `LRTOracle.updateRSETHPrice()` to set an initial rsETH price.
3. Wait 24 hours without calling `updateRSETHPrice()` again. The ETH/USD Chainlink feed continues to update normally.
4. Call `RSETHPriceFeed.latestRoundData()`.
   - Observe: `updatedAt` is recent (from the ETH/USD feed, e.g., 5 minutes ago).
   - Observe: `answer` uses the 24-hour-old rsETH price.
5. A lending protocol checking `block.timestamp - updatedAt < 3600` passes the staleness check and accepts the stale rsETH/USD price as valid collateral valuation. [2](#0-1) [5](#0-4)

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L26-26)
```text
contract RSETHPriceFeed is AggregatorV3Interface {
```

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
