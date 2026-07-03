### Title
No Staleness Check on Chainlink `latestRoundData()` Allows Stale Price to Inflate rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `price`, performing no staleness validation. A stale (inflated) LST/ETH price flows directly into the rsETH mint calculation, allowing any depositor to receive more rsETH than their deposit is worth, causing protocol insolvency.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The `updatedAt` timestamp and `answeredInRound` values are silently discarded. There is no check such as:
- `if (answeredInRound < roundId) revert StalePrice();`
- `if (updatedAt == 0 || block.timestamp - updatedAt > MAX_DELAY) revert StalePrice();`

The protocol's own `ChainlinkOracleForRSETHPoolCollateral` (used for the RSETHPool collateral path) already implements exactly these guards:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L27-32
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

`ChainlinkPriceOracle` is the oracle registered in `LRTOracle.assetPriceOracle` for supported LSTs. Its output is consumed by `LRTOracle.getAssetPrice()`, which is called by `LRTDepositPool.getRsETHAmountToMint()`:

```solidity
// contracts/LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

This is the exact formula used to mint rsETH for every `depositAsset()` and `depositETH()` call.

---

### Impact Explanation

**Critical — Protocol insolvency.**

If a Chainlink LST/ETH feed becomes stale (e.g., during network congestion, oracle downtime, or a rapid market crash), the last reported price — which may be significantly higher than the true current price — is used without question. A depositor calling `depositAsset()` with a stale inflated price receives more rsETH than the deposited asset is worth. When the oracle eventually updates to the real lower price, the outstanding rsETH supply is backed by less ETH value than it represents, leaving the protocol insolvent. All existing rsETH holders suffer dilution and bad debt.

---

### Likelihood Explanation

Chainlink feeds have documented heartbeat intervals (e.g., 1 hour or 24 hours for LST/ETH feeds on mainnet). During periods of high network congestion, oracle node failures, or rapid price drops (exactly when staleness is most dangerous), the feed can lag significantly behind the true market price. This is a well-known, historically observed failure mode. No special attacker capability is required — any user who happens to deposit during a stale window benefits at the protocol's expense.

---

### Recommendation

Add staleness and validity checks in `ChainlinkPriceOracle.getAssetPrice()`, mirroring what `ChainlinkOracleForRSETHPoolCollateral` already does:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optionally: if (block.timestamp - updatedAt > MAX_STALENESS_DELAY) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

---

### Proof of Concept

1. Chainlink LST/ETH feed for, say, stETH becomes stale. Last reported price: 1.05 ETH. True current price: 0.90 ETH (a 14% drop during a market event).
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0)`.
3. `getRsETHAmountToMint()` computes: `rsethAmountToMint = (1000e18 * 1.05e18) / rsETHPrice`. Attacker receives ~16.7% more rsETH than the stETH is actually worth.
4. Oracle updates. `updateRSETHPrice()` is called; the new rsETH price reflects the true lower asset value.
5. All prior rsETH holders are diluted. The protocol holds 1000 stETH worth 900 ETH but has issued rsETH claims worth 1050 ETH — bad debt of 150 ETH per 1000 stETH deposited.

**Root cause line:** [1](#0-0) 

**Contrast with guarded path:** [2](#0-1) 

**Mint formula consuming the stale price:** [3](#0-2) 

**Oracle dispatch:** [4](#0-3)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-52)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-32)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```
