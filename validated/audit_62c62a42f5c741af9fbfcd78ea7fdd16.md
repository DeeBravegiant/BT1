### Title
`RSETHPriceFeed.latestRoundData()` Returns ETH/USD `updatedAt` for a Composite Answer That Includes a Separately-Cached rsETH Price, Masking Staleness of the rsETH Component - (File: `contracts/oracles/RSETHPriceFeed.sol`)

---

### Summary

`RSETHPriceFeed` implements `AggregatorV3Interface` and is intended to be consumed by external integrators as a Chainlink-compatible rsETH/USD price feed. Its `latestRoundData()` computes `answer` as the product of the ETH/USD Chainlink price and `LRTOracle.rsETHPrice`, but returns `updatedAt` exclusively from the ETH/USD Chainlink feed. Because `LRTOracle.rsETHPrice` is a stored state variable updated only on explicit calls to `updateRSETHPrice()`, the `updatedAt` timestamp returned by `RSETHPriceFeed` does not reflect the freshness of the rsETH component of the answer. Integrators performing standard Chainlink staleness checks will always pass, even when the rsETH price is arbitrarily stale.

---

### Finding Description

`RSETHPriceFeed.latestRoundData()` is:

```solidity
function latestRoundData()
    external view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
``` [1](#0-0) 

The `answer` is a composite: `rsETHPrice` (from `LRTOracle`) × ETH/USD (from Chainlink). The metadata fields `roundId`, `startedAt`, `updatedAt`, and `answeredInRound` are taken entirely from the ETH/USD Chainlink feed and have no relationship to when `rsETHPrice` was last updated.

`LRTOracle.rsETHPrice` is a plain storage variable:

```solidity
uint256 public override rsETHPrice;
``` [2](#0-1) 

It is only mutated inside `_updateRsETHPrice()`, which is called by the public `updateRSETHPrice()` or the manager-gated `updateRSETHPriceAsManager()`: [3](#0-2) 

There is no on-chain mechanism that guarantees `updateRSETHPrice()` is called within any bounded window. If the keeper stops calling it, `rsETHPrice` can remain at its last stored value indefinitely, while the ETH/USD Chainlink feed continues to update normally. Every call to `RSETHPriceFeed.latestRoundData()` will return a recent `updatedAt` (from the ETH/USD heartbeat), giving integrators a false signal of freshness for the composite rsETH/USD answer.

The same defect exists in `getRoundData()`: [4](#0-3) 

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value (within the LRT-rsETH protocol itself).**

`RSETHPriceFeed` is a Chainlink-compatible wrapper explicitly designed for external integrators (the description field is set to "RSETH / USD"). Any integrator applying the standard staleness guard:

```solidity
(, int256 price,, uint256 updatedAt,) = priceFeed.latestRoundData();
if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();
```

will always pass the check because `updatedAt` tracks the ETH/USD Chainlink heartbeat (typically 1 hour), not the rsETH price update cadence. The integrator will consume a stale rsETH/USD price without any on-chain signal that the rsETH component is outdated. This breaks the contract's promise of being a correct Chainlink-compatible feed and can cause downstream mispricing in any protocol that integrates it.

---

### Likelihood Explanation

The ETH/USD Chainlink feed updates on its own heartbeat regardless of whether `updateRSETHPrice()` is called. Any period during which the keeper does not call `updateRSETHPrice()` — including during the `LRTOracle` pause, keeper downtime, or gas price spikes — will silently produce a stale composite answer while `updatedAt` remains current. This is a realistic operational scenario.

---

### Recommendation

`latestRoundData()` (and `getRoundData()`) should also track and expose the timestamp at which `rsETHPrice` was last updated in `LRTOracle`. The simplest fix is to add a `rsETHPriceUpdatedAt` storage variable to `LRTOracle` that is written alongside `rsETHPrice`, expose it via the `IRSETHOracle` interface, and return `min(ethToUsdUpdatedAt, rsETHPriceUpdatedAt)` as `updatedAt` in `RSETHPriceFeed`. This ensures the returned `updatedAt` reflects the staleness of the least-fresh component of the composite answer.

---

### Proof of Concept

1. `updateRSETHPrice()` is called at time `T`, setting `LRTOracle.rsETHPrice = P`.
2. No further calls to `updateRSETHPrice()` occur for 24 hours (keeper downtime or oracle pause).
3. At time `T + 24h`, an integrator calls `RSETHPriceFeed.latestRoundData()`.
4. The ETH/USD Chainlink feed has updated normally; `updatedAt` returned is `T + 24h - 30min` (within the 1-hour heartbeat).
5. The integrator's staleness check (`block.timestamp - updatedAt < 1 hour`) passes.
6. The integrator uses `answer = P_stale × ETH_USD_current / 1e18`, where `P_stale` is 24 hours old.
7. The integrator has no on-chain way to detect that the rsETH component is stale, because `updatedAt` only reflects the ETH/USD feed. [1](#0-0) [5](#0-4)

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

**File:** contracts/LRTOracle.sol (L87-96)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }

    /// @dev update rsETH price as an manager account
    /// @dev main benefit is to be able to update the price in case of the price going above the threshold
    /// @dev only LRT manager is allowed to call this function
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```
