Audit Report

## Title
Missing Staleness Validation on `latestRoundData()` Enables Stale Price to Over-Mint rsETH - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards `updatedAt`, `roundId`, and `answeredInRound`, performing zero staleness validation. A stale (inflated) LST/ETH price flows directly into `LRTDepositPool.getRsETHAmountToMint()`, allowing any depositor to receive more rsETH than their deposit is worth, causing protocol insolvency and dilution of all existing rsETH holders.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the price as:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All staleness-relevant return values (`roundId`, `updatedAt`, `answeredInRound`) are silently discarded. No check is performed for:
- `answeredInRound < roundId` (incomplete/stale round)
- `updatedAt == 0` (incomplete round)
- `block.timestamp - updatedAt > MAX_DELAY` (heartbeat staleness)
- `price <= 0` (invalid price)

The same codebase already implements all of these guards in `ChainlinkOracleForRSETHPoolCollateral.getRate()` (L27–32), demonstrating the protocol's own awareness of the requirement.

The stale price propagates through `LRTOracle.getAssetPrice()` (L156–157), which dispatches to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)`, and is consumed by `LRTDepositPool.getRsETHAmountToMint()` (L520):

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

This formula is executed on every `depositAsset()` and `depositETH()` call by any unprivileged user. No access control, no slippage guard on the oracle side, and no circuit breaker prevents a stale price from being used.

## Impact Explanation

**Critical — Protocol insolvency.**

If a Chainlink LST/ETH feed becomes stale with an inflated last price, every depositor calling `depositAsset()` during the stale window receives more rsETH than the deposited asset is worth. When the oracle eventually updates to the true lower price and `updateRSETHPrice()` is called, the rsETH supply is backed by less ETH value than it represents. All prior rsETH holders suffer dilution and the protocol carries bad debt proportional to the magnitude of the stale price deviation and the volume deposited during the stale window.

## Likelihood Explanation

Chainlink LST/ETH feeds on mainnet have documented heartbeat intervals of 1–24 hours. During high network congestion, oracle node failures, or rapid price drops — precisely the conditions under which staleness is most dangerous — feeds can lag significantly. No special attacker capability is required: any user who deposits during a stale window benefits at the protocol's expense. The condition is passively exploitable and historically observed across DeFi protocols.

## Recommendation

Add staleness and validity checks in `ChainlinkPriceOracle.getAssetPrice()`, mirroring `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > MAX_STALENESS_DELAY) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_STALENESS_DELAY` should be set per-feed based on the Chainlink heartbeat (e.g., 3600s for 1-hour feeds, 86400s for 24-hour feeds).

## Proof of Concept

**Setup:** Fork mainnet. Register a mock Chainlink aggregator for stETH that returns a fixed stale price of 1.05e18 with `updatedAt = block.timestamp - 2 days` and `answeredInRound < roundId`.

**Steps:**
1. Deploy/fork the protocol with `ChainlinkPriceOracle` pointing to the stale mock feed for stETH.
2. Call `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")` as an unprivileged attacker.
3. `getRsETHAmountToMint()` computes `rsethAmountToMint = (1000e18 * 1.05e18) / rsETHPrice` — attacker receives rsETH priced at 1.05 ETH/stETH.
4. Advance time; update the mock feed to the true price of 0.90e18.
5. Call `LRTOracle.updateRSETHPrice()`. The new rsETH price reflects the true lower asset value.
6. Assert: the protocol holds 1000 stETH worth 900 ETH but has issued rsETH claims worth 1050 ETH — 150 ETH bad debt per 1000 stETH deposited. All prior rsETH holders are diluted.

**Foundry invariant test:** Assert `totalRsETHSupply * rsETHPrice <= totalETHValueOfAssets` at all times. The invariant breaks as soon as a deposit is made with a stale inflated price.