### Title
`RSETHPriceFeed::latestRoundData()` Returns Stale rsETH Price Masked by Fresh ETH/USD `updatedAt` Timestamp - (File: contracts/oracles/RSETHPriceFeed.sol)

---

### Summary

`RSETHPriceFeed.latestRoundData()` computes its `answer` using the stored `LRTOracle.rsETHPrice` value, but returns the `updatedAt` timestamp sourced from the underlying ETH/USD Chainlink feed. Because these two values have completely independent update cadences, any consumer performing a standard Chainlink staleness check on `updatedAt` will see a fresh timestamp while silently consuming a stale rsETH price component.

---

### Finding Description

`RSETHPriceFeed` is a Chainlink-compatible price feed that computes rsETH/USD by multiplying the stored `rsETHPrice` from `LRTOracle` by the live ETH/USD price from a Chainlink aggregator.

The `latestRoundData()` implementation is:

```solidity
// contracts/oracles/RSETHPriceFeed.sol lines 63-70
function latestRoundData()
    external
    view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

The `updatedAt` field is taken verbatim from `ETH_TO_USD.latestRoundData()` — the ETH/USD Chainlink feed, which updates on a heartbeat of roughly 1 hour. However, the `answer` is computed using `RS_ETH_ORACLE.rsETHPrice()`, which is the stored `rsETHPrice` state variable in `LRTOracle`. This value is only updated when `updateRSETHPrice()` is explicitly called:

```solidity
// contracts/LRTOracle.sol line 87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

`rsETHPrice` is a stored state variable:

```solidity
// contracts/LRTOracle.sol line 28
uint256 public override rsETHPrice;
```

and is only written at the end of `_updateRsETHPrice()`:

```solidity
// contracts/LRTOracle.sol line 313
rsETHPrice = newRsETHPrice;
```

There is no timestamp recorded alongside `rsETHPrice` in `LRTOracle`, and `RSETHPriceFeed` makes no attempt to track or expose when `rsETHPrice` was last updated. The `updatedAt` returned to callers always reflects the ETH/USD feed's last update, not the rsETH price's last update.

The same flaw exists in `getRoundData()`:

```solidity
// contracts/oracles/RSETHPriceFeed.sol lines 53-61
function getRoundData(uint80 _roundId)
    external
    view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

Here, historical ETH/USD round data is combined with the *current* `rsETHPrice`, producing a nonsensical historical answer.

---

### Impact Explanation

Any external protocol (lending market, derivatives platform, vault) that integrates `RSETHPriceFeed` as a Chainlink-compatible oracle and performs the standard staleness check:

```solidity
require(block.timestamp - updatedAt <= MAX_STALENESS, "stale price");
```

will pass the check as long as the ETH/USD feed is fresh (which it nearly always is), even if `rsETHPrice` in `LRTOracle` has not been updated for hours or days. The consumer receives a stale rsETH/USD price with no indication of staleness.

Concretely:
- If `rsETHPrice` is stale-high (e.g., before a slashing event is reflected), borrowers can extract more debt than their collateral warrants, leading to undercollateralized positions.
- If `rsETHPrice` is stale-low, legitimate positions may be incorrectly liquidated.

**Impact: Low — Contract fails to deliver promised returns (accurate, fresh rsETH/USD price) without direct in-protocol fund loss, but enables downstream fund loss in integrating protocols.**

---

### Likelihood Explanation

`updateRSETHPrice()` is a public function but requires an active caller (keeper, bot, or user). During:
- `LRTOracle` pause periods (the `whenNotPaused` modifier blocks updates),
- keeper downtime or network congestion,
- periods where `pricePercentageLimit` causes reverts for non-managers,

`rsETHPrice` can go stale for extended periods. Meanwhile, the ETH/USD Chainlink feed continues updating normally, so `updatedAt` remains fresh and staleness checks pass silently.

**Likelihood: Medium** — keeper failure or oracle pause are realistic operational scenarios.

---

### Recommendation

`RSETHPriceFeed` should track and expose the timestamp of the last `rsETHPrice` update independently. The `updatedAt` returned by `latestRoundData()` should be `min(ethUsdUpdatedAt, rsEthPriceUpdatedAt)` so that consumers see the true freshness of the composite price. `LRTOracle` should expose a `rsETHPriceLastUpdated` timestamp, and `RSETHPriceFeed` should use it:

```solidity
function latestRoundData() external view returns (...) {
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    uint256 rsEthUpdatedAt = RS_ETH_ORACLE.rsETHPriceLastUpdated();
    updatedAt = updatedAt < rsEthUpdatedAt ? updatedAt : rsEthUpdatedAt; // min
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

---

### Proof of Concept

1. `LRTOracle.updateRSETHPrice()` is called at time `T`. `rsETHPrice` is set to `1.05e18`. No timestamp is stored.
2. Time advances by 24 hours. `updateRSETHPrice()` is not called (keeper down, or oracle paused).
3. The ETH/USD Chainlink feed updates normally every hour. At time `T + 24h`, `updatedAt` from ETH/USD is `T + 24h`.
4. A lending protocol calls `RSETHPriceFeed.latestRoundData()`.
5. It receives `updatedAt = T + 24h` (fresh) and `answer = 1.05e18 * ethPrice / 1e18` (stale rsETH component).
6. The lending protocol's staleness check passes. It accepts the stale rsETH/USD price as current.
7. If the true rsETH price has dropped to `1.00e18` (e.g., due to slashing), borrowers can borrow against inflated collateral, creating undercollateralized positions. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
