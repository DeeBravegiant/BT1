Audit Report

## Title
`RSETHPriceFeed::latestRoundData()` Returns Stale rsETH Price Masked by Fresh ETH/USD `updatedAt` Timestamp - (File: contracts/oracles/RSETHPriceFeed.sol)

## Summary
`RSETHPriceFeed.latestRoundData()` computes `answer` using `LRTOracle.rsETHPrice` — a stored state variable updated only when `updateRSETHPrice()` is explicitly called — but returns `updatedAt` sourced verbatim from the ETH/USD Chainlink feed, which updates on its own independent heartbeat. Any consumer performing a standard Chainlink staleness check on `updatedAt` will see a fresh timestamp while silently consuming a stale rsETH price component. The contract fails to deliver the accurate, fresh composite price it is designed to provide.

## Finding Description
In `contracts/oracles/RSETHPriceFeed.sol` lines 63–70:

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

`updatedAt` is taken directly from `ETH_TO_USD.latestRoundData()` (the Chainlink ETH/USD feed, ~1 hour heartbeat). The `answer` is computed using `RS_ETH_ORACLE.rsETHPrice()`, which reads `LRTOracle.rsETHPrice` — a storage variable at `contracts/LRTOracle.sol` line 28 — written only at line 313 inside `_updateRsETHPrice()`. There is no timestamp stored alongside `rsETHPrice`, and `RSETHPriceFeed` makes no attempt to track or expose when `rsETHPrice` was last updated.

`updateRSETHPrice()` (line 87) is public but gated by `whenNotPaused`. During any pause of `LRTOracle`, `rsETHPrice` cannot be updated while the ETH/USD Chainlink feed continues refreshing normally, keeping `updatedAt` perpetually fresh. The same flaw exists in `getRoundData()` (lines 53–61), which additionally combines historical ETH/USD round data with the *current* `rsETHPrice`, producing a nonsensical historical answer.

Existing guards are insufficient: the `whenNotPaused` modifier on `updateRSETHPrice()` is precisely the condition that causes staleness, and there is no on-chain mechanism to detect or signal that `rsETHPrice` has not been refreshed.

## Impact Explanation
**Low — Contract fails to deliver promised returns.**

`RSETHPriceFeed` is a Chainlink-compatible oracle whose contract interface promises a composite rsETH/USD price with a meaningful `updatedAt` staleness indicator. It fails to deliver this: the returned `updatedAt` reflects only the ETH/USD feed's freshness, not the rsETH component's freshness. Any integrating protocol performing the standard staleness check (`block.timestamp - updatedAt <= MAX_STALENESS`) will accept a stale rsETH/USD price as current. This directly matches the allowed impact: *"Low. Contract fails to deliver promised returns, but doesn't lose value."*

## Likelihood Explanation
`rsETHPrice` staleness is a realistic operational scenario requiring no attacker action:
- During any `LRTOracle` pause (triggered by `pricePercentageLimit` downside protection at line 280, or by a PAUSER_ROLE holder), `updateRSETHPrice()` reverts for all callers.
- During keeper downtime or network congestion, no one calls `updateRSETHPrice()`.
- In both cases, the ETH/USD Chainlink feed continues updating normally, so `updatedAt` remains fresh and staleness checks pass silently.

These are routine operational conditions, not exotic attack scenarios.

## Recommendation
`LRTOracle` should expose a `rsETHPriceLastUpdated` timestamp, set alongside `rsETHPrice = newRsETHPrice` at line 313. `RSETHPriceFeed.latestRoundData()` should then return `updatedAt = min(ethUsdUpdatedAt, rsETHPriceLastUpdated)`:

```solidity
function latestRoundData() external view returns (...) {
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    uint256 rsEthUpdatedAt = RS_ETH_ORACLE.rsETHPriceLastUpdated();
    if (rsEthUpdatedAt < updatedAt) updatedAt = rsEthUpdatedAt;
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

`getRoundData()` should be deprecated or similarly corrected, as combining historical ETH/USD round data with the current `rsETHPrice` is semantically incorrect.

## Proof of Concept
1. `LRTOracle.updateRSETHPrice()` is called at time `T`. `rsETHPrice` is set to `1.05e18`. No timestamp is stored alongside it.
2. `LRTOracle` is paused (e.g., via automatic downside protection at line 280, or by a PAUSER_ROLE holder). `updateRSETHPrice()` now reverts for all callers due to `whenNotPaused`.
3. Time advances 24 hours. The ETH/USD Chainlink feed updates normally every ~1 hour. At `T + 24h`, `ETH_TO_USD.latestRoundData()` returns `updatedAt = T + 24h`.
4. A lending protocol calls `RSETHPriceFeed.latestRoundData()`. It receives `updatedAt = T + 24h` (fresh) and `answer = 1.05e18 * ethPrice / 1e18` (stale rsETH component).
5. The lending protocol's staleness check (`block.timestamp - updatedAt <= MAX_STALENESS`) passes. It accepts the stale rsETH/USD price as current.
6. If the true rsETH price has dropped (e.g., due to slashing reflected in underlying assets), the stale high price is used, enabling overborrowing against inflated collateral.

**Foundry fork test outline:**
```solidity
// 1. Deploy RSETHPriceFeed pointing to live ETH/USD feed and a mock LRTOracle
// 2. Set mock rsETHPrice = 1.05e18, record block.timestamp as T
// 3. vm.warp(T + 25 hours)
// 4. Call latestRoundData(); assert updatedAt >= T + 24h (fresh from ETH/USD)
// 5. Assert rsETHPrice component is still 1.05e18 (stale — never updated)
// 6. Demonstrate staleness check passes despite 25-hour-old rsETH price
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
