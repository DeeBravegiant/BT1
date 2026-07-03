Audit Report

## Title
Chainlink Price Feed Staleness Not Validated in `ChainlinkPriceOracle.getAssetPrice()`, Enabling Stale-Price-Based rsETH Over-Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards `updatedAt`, `answeredInRound`, and `roundId`, and performs no `price > 0` guard. A stale Chainlink feed silently returns its last known price, which is consumed directly by `LRTDepositPool.getRsETHAmountToMint()` to compute how many rsETH tokens to mint for a depositor. If the stale price exceeds the true market price, every depositor receives more rsETH than their assets are worth, continuously diluting existing rsETH holders until the feed recovers.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches round data but uses only `price`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
``` [1](#0-0) 

The sibling oracle in the same repository performs all three missing checks:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L27-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The exploit path for the stale-price scenario:

1. `LRTDepositPool.depositAsset()` calls `_beforeDeposit()` → `getRsETHAmountToMint()`.
2. `getRsETHAmountToMint()` calls `lrtOracle.getAssetPrice(asset)` and divides by `lrtOracle.rsETHPrice()`. [3](#0-2) 
3. `LRTOracle.getAssetPrice()` delegates to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)`, which resolves to `ChainlinkPriceOracle.getAssetPrice()`. [4](#0-3) 
4. The stale price is returned without any staleness check and used to compute `rsethAmountToMint`.

The `pricePercentageLimit` guard in `_updateRsETHPrice()` does not protect the deposit path: it only gates the oracle price-update function, not `getRsETHAmountToMint()`. [5](#0-4) 

Regarding the negative-price edge case: Solidity 0.8.27 has checked arithmetic. Casting a negative `int256` to `uint256` is a valid bitwise reinterpretation (not an arithmetic operation), so it does not revert — it produces a near-`type(uint256).max` value. However, the subsequent multiplication `amount * lrtOracle.getAssetPrice(asset)` in `getRsETHAmountToMint` would overflow and revert under checked arithmetic, limiting the negative-price scenario to a denial-of-service rather than fund theft. The stale-price scenario (realistic) is the primary valid impact.

## Impact Explanation

**High — Theft of unclaimed yield.**

When a Chainlink feed for a supported LST (stETH, ETHx, rETH, sfrxETH, swETH) becomes stale at a price above the true market price, every call to `depositAsset()` mints excess rsETH proportional to the price discrepancy. The excess rsETH is backed by fewer real assets than implied, diluting the ETH value redeemable by all existing rsETH holders. This constitutes continuous theft of unclaimed yield from existing holders for as long as the feed remains stale and deposits continue.

## Likelihood Explanation

Chainlink feeds have documented staleness incidents (e.g., during the March 2023 USDC depeg). The protocol supports five LST assets, each with its own feed; the probability that at least one feed experiences a staleness event over the protocol's lifetime is non-trivial. No heartbeat duration is enforced anywhere in `ChainlinkPriceOracle`. The entry path requires no privilege — any user calling `depositAsset()` triggers the vulnerable code path. The `minRSETHAmountExpected` slippage parameter is set by the depositor and provides no protection against over-minting.

## Recommendation

Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral.sol`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optionally enforce a per-feed heartbeat:
    // if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

## Proof of Concept

1. Deploy a mock Chainlink aggregator for stETH/ETH that returns `price = 1.05e8` (stale, 5% above true market) with `updatedAt` set to 4 hours ago and `answeredInRound == roundId` (so the feed appears technically valid but stale).
2. Register this mock as the price feed for stETH in `ChainlinkPriceOracle` via `updatePriceFeedFor`.
3. Call `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")` as an unprivileged attacker.
4. Observe `getRsETHAmountToMint` returns `(1000e18 * 1.05e18) / rsETHPrice` — approximately 5% more rsETH than the deposit is worth.
5. Repeat until the feed recovers; each deposit extracts yield from existing rsETH holders.

Foundry fork test plan: fork mainnet, warp `block.timestamp` forward by 4 hours past the last `updatedAt` of the stETH/ETH Chainlink feed, call `depositAsset`, and assert that `rsethAmountToMint > (depositAmount * truePrice) / rsETHPrice`.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-32)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L252-266)
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
```
