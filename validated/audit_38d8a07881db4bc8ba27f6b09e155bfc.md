### Title
`ChainlinkPriceOracle.getAssetPrice` Does Not Validate Staleness or Zero/Negative Price — (`contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice` calls `latestRoundData()` but discards `updatedAt` and never validates that the returned `price` is positive. This mirrors the Tellor best-practices gap in the reference report: no freshness check and no zero-value guard.

---

### Finding Description

`getAssetPrice` in `ChainlinkPriceOracle.sol` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

Two checks are absent:

1. **No staleness check** — `updatedAt` is silently discarded. If a Chainlink feed stops updating (network congestion, sequencer downtime on L2, feed deprecation), the last stale price is returned indefinitely with no revert.

2. **No zero/negative price check** — `price` is cast directly to `uint256` without verifying `price > 0`. A zero answer returns `0` to callers; a negative answer silently wraps to a near-`type(uint256).max` value.

This price is consumed directly by `LRTOracle.getAssetPrice`, which is called in the deposit path:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

and in the swap-return-amount helpers:

```solidity
return lrtOracle.getAssetPrice(fromAsset) * fromAssetAmount / 1e18;
``` [3](#0-2) 

---

### Impact Explanation

| Scenario | Effect |
|---|---|
| Stale price (feed stops updating) | Depositors mint rsETH at an incorrect rate; protocol TVL accounting diverges from reality |
| Zero price returned | `rsethAmountToMint = 0`; depositor loses their LST with no rsETH minted — temporary freeze of deposited funds |
| Negative price (feed malfunction) | `uint256(negative)` wraps to ~`2^256`, causing arithmetic overflow in the multiplication, reverting all deposits |

The zero-price path is the most directly exploitable: any depositor calling `depositAsset` during a period when the Chainlink feed returns 0 receives 0 rsETH, effectively losing their deposit until the oracle recovers.

**Impact: Medium — Temporary freezing of funds / contract fails to deliver promised returns.**

---

### Likelihood Explanation

Chainlink feeds can return stale or zero data during:
- L2 sequencer downtime (relevant if the protocol is deployed on L2 chains, which the pool contracts suggest)
- Feed deprecation or migration
- Extreme market volatility causing heartbeat gaps

No attacker action is required; this is triggered by ordinary depositor/swapper interactions during any oracle degradation window.

---

### Recommendation

Apply standard Chainlink best-practice guards in `getAssetPrice`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price, , uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    require(price > 0, "Invalid price");
    require(updatedAt >= block.timestamp - STALENESS_THRESHOLD, "Stale price");
    require(answeredInRound >= roundId, "Incomplete round");

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`STALENESS_THRESHOLD` should be set per-feed based on the feed's documented heartbeat (e.g., 3600 s for a 1-hour heartbeat feed).

---

### Proof of Concept

1. Chainlink feed for `stETH/ETH` stops updating (sequencer outage or feed issue).
2. `latestRoundData()` returns the last cached price with a stale `updatedAt`, but the contract accepts it unconditionally.
3. A depositor calls `LRTDepositPool.depositAsset(stETH, amount, minRsETH)`.
4. `getAssetPrice(stETH)` returns the stale price; `rsethAmountToMint` is computed from it.
5. If the stale price is lower than the true price, the depositor receives fewer rsETH than entitled; if the feed returns 0, they receive 0 rsETH and their `stETH` is transferred in with no receipt. [4](#0-3)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L560-560)
```text
        return lrtOracle.getAssetPrice(fromAsset) * fromAssetAmount / 1e18;
```
