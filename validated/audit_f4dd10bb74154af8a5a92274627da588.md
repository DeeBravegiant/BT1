Audit Report

## Title
No Staleness Check on Chainlink `latestRoundData()` Allows Stale Price to Inflate rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards `updatedAt` and `answeredInRound`, performing no staleness validation. A stale (inflated) LST/ETH price flows directly into `LRTDepositPool.getRsETHAmountToMint()`, allowing any depositor to receive more rsETH than their deposit is worth. This causes protocol insolvency and dilutes all existing rsETH holders.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the price as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

The `updatedAt` timestamp and `answeredInRound` are silently discarded. No check of the form `if (answeredInRound < roundId) revert StalePrice()` or `if (block.timestamp - updatedAt > MAX_DELAY) revert StalePrice()` is present.

The protocol's own `ChainlinkOracleForRSETHPoolCollateral` already implements exactly these guards:

```solidity
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

`ChainlinkPriceOracle` is the oracle registered in `LRTOracle.assetPriceOracle` for supported LSTs. Its output is consumed by `LRTOracle.getAssetPrice()`: [3](#0-2) 

Which is called by `LRTDepositPool.getRsETHAmountToMint()`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [4](#0-3) 

This formula is used for every `depositAsset()` and `depositETH()` call. A stale inflated price directly inflates `rsethAmountToMint`, minting more rsETH than the deposited asset is worth.

## Impact Explanation

**Critical — Protocol insolvency.** If a Chainlink LST/ETH feed becomes stale (e.g., last reported price 1.05 ETH, true price 0.90 ETH), every depositor during the stale window receives rsETH claims exceeding the real value of their deposit. When the oracle updates, the rsETH supply is backed by less ETH value than it represents. All existing rsETH holders suffer dilution and the protocol accumulates bad debt. This matches the allowed critical impact of "Protocol insolvency."

## Likelihood Explanation

Chainlink LST/ETH feeds have documented heartbeat intervals (1–24 hours on mainnet). During network congestion, oracle node failures, or rapid price drops — exactly the conditions when staleness is most dangerous — feeds can lag significantly. No special attacker capability is required: any user who calls `depositAsset()` or `depositETH()` during a stale window benefits at the protocol's expense. The condition is historically observed and repeatable.

## Recommendation

Add staleness and validity checks in `ChainlinkPriceOracle.getAssetPrice()`, mirroring `ChainlinkOracleForRSETHPoolCollateral`:

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

## Proof of Concept

**Foundry fork test plan:**

1. Fork mainnet at a block where a Chainlink LST/ETH feed (e.g., stETH/ETH) is within its heartbeat window.
2. `vm.warp(block.timestamp + 25 hours)` to simulate a stale feed (no new round published).
3. Call `LRTDepositPool.depositAsset(stETH, 1000e18, 0)` as an unprivileged address.
4. Assert that `rsethAmountToMint` is computed using the stale (pre-warp) price, which is higher than the true current price.
5. Call `LRTOracle.updateRSETHPrice()` to reflect the real lower asset value.
6. Assert that the rsETH price has dropped, proving the depositor received more rsETH than the deposited stETH is worth — bad debt is now embedded in the protocol.

**Minimal call sequence (no fork):**

1. Deploy a mock `AggregatorV3Interface` returning `answeredInRound < roundId` and a stale `updatedAt`.
2. Register it via `ChainlinkPriceOracle.updatePriceFeedFor()`.
3. Call `ChainlinkPriceOracle.getAssetPrice(asset)` — it returns the stale price without reverting, confirming the missing guard.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-32)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
