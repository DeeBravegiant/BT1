Audit Report

## Title
Missing Chainlink Price Feed Staleness Validation Allows Stale Prices to Corrupt rsETH Minting and Price Updates - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `price`, performing no staleness, incomplete-round, or zero-price checks. A stale or invalid price is silently accepted and propagated into rsETH minting calculations and the public `updateRSETHPrice()` function, enabling dilution of existing rsETH holders' yield.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` at line 52 ignores `roundId`, `updatedAt`, and `answeredInRound`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

The same repository already implements all three required checks in `ChainlinkOracleForRSETHPoolCollateral.getRate()`: [2](#0-1) 

The stale price propagates through two public paths:

1. `LRTDepositPool.getRsETHAmountToMint()` → `lrtOracle.getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice()`, directly determining how much rsETH is minted per deposit: [3](#0-2) 

2. `LRTOracle.updateRSETHPrice()` (public, no access control) → `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `getAssetPrice(asset)`, corrupting the stored `rsETHPrice`: [4](#0-3) [5](#0-4) 

The `pricePercentageLimit` guard in `_updateRsETHPrice()` is an insufficient mitigation: it is initialized to zero (no limit) and only triggers when the new price exceeds `highestRsethPrice` by more than the configured threshold — a stale price within the normal historical range passes through entirely unchecked. [6](#0-5) 

## Impact Explanation
**High — Theft of unclaimed yield.**

When a Chainlink LST/ETH feed goes stale with an inflated last price, any depositor calling `depositAsset()` receives excess rsETH relative to the true value of their deposit. This excess minted supply dilutes the rsETH held by all existing depositors, directly reducing the ETH value redeemable per rsETH token — constituting theft of accumulated yield from existing rsETH holders. The effect is compounded if `updateRSETHPrice()` is called while the stale price is active, writing an incorrect `rsETHPrice` that affects all subsequent minting and withdrawal calculations.

## Likelihood Explanation
**Medium.** Chainlink feeds have documented heartbeat intervals (24 h on mainnet, 1 h on some L2s). Sequencer downtime on L2 deployments, network congestion, or feed-specific issues can cause staleness within those windows. No special permissions are required: `depositAsset()` and `updateRSETHPrice()` are both callable by any unprivileged external account. The scenario is repeatable whenever a feed lags its heartbeat.

## Recommendation
Apply the same three checks already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, add a per-feed configurable `maxStaleness` parameter and revert if `block.timestamp - updatedAt > maxStaleness`, sized to each feed's heartbeat plus a safety buffer.

## Proof of Concept
1. Deploy a mock Chainlink aggregator for stETH/ETH that returns a fixed stale price of `1.05e18` (last updated 25+ hours ago).
2. Register it via `ChainlinkPriceOracle.updatePriceFeedFor(stETH, mockFeed)`.
3. Call `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")` — `getRsETHAmountToMint` computes `(1000e18 * 1.05e18) / rsETHPrice`, minting ~5% excess rsETH.
4. Verify existing rsETH holders' redemption value per token has decreased proportionally.
5. Call `LRTOracle.updateRSETHPrice()` — `_getTotalEthInProtocol()` uses the stale price, writing an inflated `rsETHPrice` to storage, corrupting all future minting calculations.
6. Confirm no revert occurs at any step due to the absent staleness checks in `ChainlinkPriceOracle.getAssetPrice()`.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L252-267)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
        }
```

**File:** contracts/LRTOracle.sol (L336-344)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

```
