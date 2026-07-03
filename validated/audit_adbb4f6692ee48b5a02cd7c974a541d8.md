### Title
`RSETHPriceFeed.latestRoundData()` Returns Stale rsETH Price With Fresh `updatedAt` Timestamp - (File: contracts/oracles/RSETHPriceFeed.sol)

### Summary
`RSETHPriceFeed` is a Chainlink-compatible price feed that computes the rsETH/USD price by multiplying the rsETH/ETH rate (from `RS_ETH_ORACLE`) with the ETH/USD Chainlink price. However, the `updatedAt` field returned by `latestRoundData()` and `getRoundData()` is sourced exclusively from the ETH/USD Chainlink feed, not from the rsETH oracle. If the rsETH oracle price becomes stale, downstream consumers (e.g., lending protocols) that rely on `updatedAt` for freshness checks will incorrectly treat the combined price as current.

### Finding Description
In `RSETHPriceFeed.latestRoundData()`:

```solidity
function latestRoundData()
    external
    view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

The `answer` is the product of two independent values:
- `ETH_TO_USD` price (freshness tracked by `updatedAt`)
- `RS_ETH_ORACLE.rsETHPrice()` (freshness **not tracked at all**)

The `RS_ETH_ORACLE` is the `LRTOracle` contract, whose `rsETHPrice` state variable is only updated when `updateRSETHPrice()` is called off-chain. There is no on-chain mechanism in `RSETHPriceFeed` to detect or surface the age of the rsETH component. The returned `updatedAt` reflects only when the ETH/USD Chainlink feed was last updated, which can be as recent as a few seconds ago even if the rsETH price has not been updated for hours or days.

The same flaw exists in `getRoundData()`:

```solidity
(roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.getRoundData(_roundId);
answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
```

### Impact Explanation
Any lending protocol or DeFi integration that consumes `RSETHPriceFeed` as a Chainlink-compatible oracle and uses `updatedAt` to gate staleness will receive a misleading freshness signal. If the rsETH oracle price is stale and lower than the true rate (e.g., during a period of rapid restaking yield accrual), rsETH holders using those lending protocols as borrowers could be incorrectly liquidated. If the stale price is higher than the true rate, rsETH holders could over-borrow, creating bad debt. In both cases, the price feed fails to deliver the promised accurate and fresh rsETH/USD price.

**Impact: Low** — the price feed contract fails to deliver its promised behavior (accurate freshness reporting), but the direct loss mechanism is mediated through external lending integrations.

### Likelihood Explanation
The `LRTOracle.rsETHPrice` is updated by calling `updateRSETHPrice()`, which is an off-chain-triggered operation. Any operational gap (network issues, keeper failure, deliberate delay) causes the rsETH component to become stale while `updatedAt` from the ETH/USD feed continues to appear fresh. This is a realistic operational scenario, not a theoretical one.

### Recommendation
Track the rsETH oracle's last update timestamp separately and return the **minimum** of the two `updatedAt` values (ETH/USD feed and rsETH oracle) so that consumers correctly detect staleness in either component:

```solidity
function latestRoundData() external view returns (...) {
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    uint256 rsETHUpdatedAt = RS_ETH_ORACLE.lastUpdated(); // requires oracle to expose this
    updatedAt = updatedAt < rsETHUpdatedAt ? updatedAt : rsETHUpdatedAt;
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

Alternatively, add an explicit staleness check inside `RSETHPriceFeed` and revert if the rsETH oracle price is older than a configurable threshold.

### Proof of Concept

1. `LRTOracle.updateRSETHPrice()` is not called for 24 hours. `rsETHPrice` in `LRTOracle` is now stale.
2. The ETH/USD Chainlink feed continues to update normally every ~1 hour.
3. A lending protocol calls `RSETHPriceFeed.latestRoundData()`.
4. The returned `updatedAt` is from the ETH/USD feed — e.g., `block.timestamp - 300` (5 minutes ago). The protocol's staleness check passes.
5. The returned `answer` is computed using the 24-hour-old `rsETHPrice`, which is lower than the true current rate.
6. The lending protocol undervalues rsETH collateral and triggers liquidations on rsETH holders whose positions are actually healthy.

Root cause lines: [1](#0-0) 

The `updatedAt` is taken from the ETH/USD feed only, while `answer` silently incorporates the potentially stale `RS_ETH_ORACLE.rsETHPrice()` with no freshness tracking. [2](#0-1) 

The same flaw is present in `getRoundData()`.

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
