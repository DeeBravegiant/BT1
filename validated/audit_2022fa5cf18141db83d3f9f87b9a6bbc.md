### Title
Missing Chainlink Circuit-Breaker Min/Max Validation Allows Deposits at Inflated Prices During Asset Crash Events - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and returns the raw price with no validation against the aggregator's `minAnswer`/`maxAnswer` circuit-breaker bounds. If a supported LST asset crashes significantly (analogous to the LUNA/Venus/Blizz event), the Chainlink feed will silently clamp its reported price at the pre-configured `minAnswer` rather than the true market price. Because the protocol never checks for this condition, deposits will be accepted at the inflated `minAnswer` price, allowing an attacker to mint rsETH worth far more than the assets deposited, diluting all existing rsETH holders.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price and returns it directly:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

There is no check that `price` is within `[minAnswer, maxAnswer]`. Chainlink aggregators enforce a floor (`minAnswer`) and ceiling (`maxAnswer`) on the values they will report. When an asset's true market price falls below `minAnswer`, the aggregator continues to report `minAnswer` — not the actual price. Without a circuit-breaker check, the protocol treats this clamped value as a valid price.

This price propagates through the entire deposit and rsETH-price-calculation stack:

- `LRTOracle.getAssetPrice(asset)` delegates directly to `ChainlinkPriceOracle.getAssetPrice()`.
- `LRTDepositPool.getRsETHAmountToMint()` uses `lrtOracle.getAssetPrice(asset)` to compute how many rsETH tokens to mint per unit of deposited asset.
- `LRTOracle._getTotalEthInProtocol()` uses `getAssetPrice()` for every supported asset to compute the rsETH/ETH exchange rate.

`ChainlinkOracleForRSETHPoolCollateral.getRate()` (used in the pool path) has the same deficiency — it checks for staleness and non-positive price but not for min/max bounds.

### Impact Explanation
During a flash-crash of any supported LST (e.g., stETH, cbETH), the Chainlink feed reports `minAnswer` instead of the true price. An attacker deposits the crashed asset and receives rsETH calculated at the inflated `minAnswer`. The rsETH they receive is redeemable for ETH at the true rsETH/ETH rate, which is backed by the real (much lower) value of the deposited asset. This directly steals value from all existing rsETH holders by diluting the backing of the token — a form of protocol insolvency / direct theft of user funds.

**Impact: Critical — direct theft of user funds / protocol insolvency.**

### Likelihood Explanation
Chainlink circuit-breaker events are rare but have occurred in production (LUNA crash affecting Venus and Blizz Finance). Any supported LST that has a non-trivial `minAnswer` configured on its Chainlink aggregator is susceptible. The attack requires no special permissions — any depositor can trigger it the moment the asset price falls below `minAnswer`.

### Recommendation
Add a min/max bounds check in `ChainlinkPriceOracle.getAssetPrice()` and `ChainlinkOracleForRSETHPoolCollateral.getRate()`. The aggregator's `minAnswer` and `maxAnswer` can be read from the `AggregatorV2V3Interface`:

```solidity
// In ChainlinkPriceOracle.getAssetPrice():
(, int256 price,,,) = priceFeed.latestRoundData();
int192 minAnswer = IAggregator(address(priceFeed)).minAnswer();
int192 maxAnswer = IAggregator(address(priceFeed)).maxAnswer();
if (price <= int256(minAnswer) || price >= int256(maxAnswer)) {
    revert OracleCircuitBreakerTripped();
}
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Note the inclusive bounds (`<=` / `>=`): since the aggregator clamps at exactly `minAnswer`/`maxAnswer`, a strict inequality (`<`/`>`) would miss the circuit-breaker condition (the same off-by-one bug described in the reference report).

### Proof of Concept

1. Suppose stETH is a supported asset with a Chainlink feed whose `minAnswer` = `0.5e18` (0.5 ETH).
2. A black-swan event causes stETH's true market price to drop to `0.1e18`.
3. The Chainlink aggregator clamps and reports `0.5e18` (its `minAnswer`).
4. `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `0.5e18` — 5× the real price.
5. An attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18)`, depositing 1000 stETH worth ~100 ETH at market.
6. `getRsETHAmountToMint` computes `(1000e18 * 0.5e18) / rsETHPrice`, minting rsETH as if the deposit is worth 500 ETH.
7. The attacker redeems rsETH for ~500 ETH worth of assets, extracting ~400 ETH from other depositors.

The root cause is the absence of any min/max validation in `getAssetPrice()`: [1](#0-0) 

Which feeds directly into rsETH minting: [2](#0-1) 

And into the total ETH backing calculation used to price rsETH: [3](#0-2) 

The same missing check exists in the pool collateral oracle: [4](#0-3)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L336-343)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
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
