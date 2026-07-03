### Title
Missing Chainlink Circuit Breaker Min/Max Answer Validation in `getAssetPrice()` - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and uses the returned `price` with zero validation — no staleness check, no roundId check, and critically no min/max answer bounds check. Chainlink aggregators have a built-in circuit breaker that clamps the returned answer to `minAnswer`/`maxAnswer` when an asset's price moves outside a predetermined band. If an LST asset (stETH, rETH, etc.) experiences a severe depeg, the oracle will return `minAnswer` instead of the actual price, causing the protocol to compute an inflated `rsETHPrice`, which can be exploited by existing rsETH holders to redeem at an inflated rate, draining the protocol's real assets.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price with no validation whatsoever:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All five return values from `latestRoundData()` (`roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound`) are ignored except `price`. There is no check that:
- `price > 0`
- `price >= minAnswer` (circuit breaker lower bound)
- `price <= maxAnswer` (circuit breaker upper bound)
- `updatedAt` is recent (staleness)
- `answeredInRound >= roundId`

This price is consumed by `LRTOracle.getAssetPrice()`, which delegates directly to the `ChainlinkPriceOracle`: [2](#0-1) 

`LRTOracle._getTotalEthInProtocol()` iterates over all supported LST assets and multiplies each asset's total deposit amount by its oracle price: [3](#0-2) 

This total ETH value is then used in `_updateRsETHPrice()` to compute and store the new `rsETHPrice`: [4](#0-3) 

`updateRSETHPrice()` is a public function callable by anyone (when not paused): [5](#0-4) 

The same missing-bounds issue exists in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which only checks `ethPrice <= 0` but not min/max bounds: [6](#0-5) 

---

### Impact Explanation

**Critical — Protocol insolvency / direct theft of user funds.**

If an LST asset (e.g., stETH) experiences a severe depeg:

1. Chainlink's circuit breaker clamps the returned answer to `minAnswer` (e.g., 0.9 ETH) instead of the actual crashed price (e.g., 0.3 ETH).
2. `ChainlinkPriceOracle.getAssetPrice(stETH)` returns the inflated `minAnswer`.
3. `_getTotalEthInProtocol()` overstates the protocol's ETH value.
4. `_updateRsETHPrice()` computes and stores an inflated `rsETHPrice`.
5. An attacker who holds rsETH (acquired before the depeg at fair value) initiates a withdrawal at the inflated `rsETHPrice`, receiving far more underlying assets than their rsETH is actually worth.
6. The protocol's real asset backing is drained, leaving remaining rsETH holders with under-collateralized positions — protocol insolvency.

The `pricePercentageLimit` guard in `_updateRsETHPrice()` provides partial mitigation only for price increases above the configured threshold; it does not prevent the circuit-breaker-clamped price from being accepted if the inflation is within the configured limit, and it does not validate the oracle answer at the source. [7](#0-6) 

---

### Likelihood Explanation

**Medium.** LST assets (stETH, rETH, swETH, frxETH) are all subject to depeg risk. Historical events (e.g., stETH depeg in June 2022, Lido slashing scenarios) demonstrate that LST prices can move sharply. Chainlink aggregators for LST/ETH feeds do have configured `minAnswer` values. The attack requires no special permissions — any rsETH holder can initiate a withdrawal after `updateRSETHPrice()` is called with the clamped price.

---

### Recommendation

Add min/max answer bounds validation in `ChainlinkPriceOracle.getAssetPrice()`:

```diff
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

-   (, int256 price,,,) = priceFeed.latestRoundData();
+   (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();
+   require(price > 0, "Invalid price");
+   require(answeredInRound >= roundId, "Stale price");
+   require(updatedAt != 0 && block.timestamp - updatedAt <= 25 hours, "Stale round");
+   // Fetch and check circuit breaker bounds from the aggregator
+   require(price >= minAnswer && price <= maxAnswer, "Price outside circuit breaker bounds");

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Apply the same fix to `ChainlinkOracleForRSETHPoolCollateral.getRate()`. [1](#0-0) 

---

### Proof of Concept

Assume:
- Protocol holds 1000 stETH, `rsETHPrice = 1.05e18` (1.05 ETH per rsETH), `rsETH.totalSupply() = 1000e18`.
- stETH depegs: actual price drops to `0.3e18` ETH, but Chainlink `minAnswer = 0.9e18`.
- `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `0.9e18` (inflated).
- Anyone calls `LRTOracle.updateRSETHPrice()`:
  - `totalETHInProtocol = 1000e18 * 0.9e18 / 1e18 = 900e18` (actual: `300e18`)
  - `newRsETHPrice = 900e18 / 1000e18 = 0.9e18`
- Attacker holds `100e18` rsETH (acquired at fair value, worth `100 * 0.3 = 30 ETH` real value).
- Attacker initiates withdrawal; at `rsETHPrice = 0.9e18` and `assetPrice = 0.9e18`:
  - `currentReturn = (100e18 * 0.9e18) / 0.9e18 = 100e18` stETH
- Attacker receives 100 stETH (worth 30 ETH real value) instead of ~28.57 stETH (their fair share).
- The 70 stETH excess is stolen from remaining depositors, leaving the protocol insolvent.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
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
