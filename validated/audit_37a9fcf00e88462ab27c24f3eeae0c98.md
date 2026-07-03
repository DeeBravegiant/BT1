### Title
Missing Negative/Zero Price Validation and Chainlink Min/Max Circuit Breaker Check in `ChainlinkPriceOracle.getAssetPrice()` - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and directly casts the returned `int256 price` to `uint256` without checking for a negative or zero value, and without checking whether the price has been clamped to Chainlink's `minAnswer`/`maxAnswer` circuit breaker bounds. This oracle is the primary price source for computing the rsETH exchange rate in `LRTOracle._getTotalEthInProtocol()`, which is publicly triggerable.

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the asset/ETH exchange rate from a Chainlink aggregator:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

There are two missing validations:

1. **No negative/zero price check**: `price` is an `int256` but is cast directly to `uint256` without verifying `price > 0`. A zero price returns 0 (zeroing out that asset's contribution to TVL), and a negative price wraps to a near-`type(uint256).max` value, causing overflow and a revert in downstream arithmetic.

2. **No min/max circuit breaker check**: Chainlink aggregators have on-chain `minAnswer`/`maxAnswer` bounds. If a supported asset crashes in value, the aggregator returns `minAnswer` (a floor price) rather than the true market price. The code accepts this clamped value without validation.

By contrast, the sibling contract `ChainlinkOracleForRSETHPoolCollateral.getRate()` does check `if (ethPrice <= 0) revert InvalidPrice()`, demonstrating the project is aware of this pattern but did not apply it consistently. [1](#0-0) [2](#0-1) 

The price returned by `getAssetPrice()` flows directly into `LRTOracle._getTotalEthInProtocol()`, which sums `totalAssetAmt * assetER` for every supported asset, and that total is used to compute `newRsETHPrice`: [3](#0-2) [4](#0-3) 

### Impact Explanation

**Circuit breaker scenario (realistic):** If a supported LST (e.g., stETH) suffers a severe depeg and its Chainlink feed clamps to `minAnswer`, `_getTotalEthInProtocol()` returns an inflated TVL. This inflates `newRsETHPrice`, causing new depositors to receive fewer rsETH shares than they are entitled to — the protocol fails to deliver the correct exchange rate. Existing holders benefit at new depositors' expense.

**Zero price scenario:** If `price == 0`, that asset's entire TVL contribution is silently zeroed, deflating `newRsETHPrice` and causing existing rsETH holders to receive less ETH on withdrawal than they are owed.

**Impact rating: Low** — Contract fails to deliver promised returns (incorrect rsETH price computation affecting depositors and withdrawers). [5](#0-4) 

### Likelihood Explanation

Chainlink circuit breakers have triggered historically (e.g., LUNA crash). The supported assets in this protocol are LSTs whose ETH-denominated feeds could plausibly hit `minAnswer` during a severe depeg event. The entry point `updateRSETHPrice()` is public and callable by any address, so no privileged access is required to trigger the mispricing. [6](#0-5) 

### Recommendation

Add the following checks to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();

    // 1. Reject non-positive prices
    if (price <= 0) revert InvalidPrice();

    // 2. Reject prices at Chainlink circuit breaker bounds
    int192 minAnswer = IChainlinkAggregator(address(priceFeed)).minAnswer();
    int192 maxAnswer = IChainlinkAggregator(address(priceFeed)).maxAnswer();
    if (price <= minAnswer || price >= maxAnswer) revert PriceOutOfBounds();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

This mirrors the pattern already used in `ChainlinkOracleForRSETHPoolCollateral.getRate()` for the negative/zero check. [1](#0-0) 

### Proof of Concept

1. A supported LST asset (e.g., stETH) suffers a severe depeg event.
2. Chainlink's on-chain aggregator clamps the returned price to `minAnswer` (e.g., 0.5e18 ETH) instead of the true market price (e.g., 0.01e18 ETH).
3. Any caller invokes `LRTOracle.updateRSETHPrice()` (public, no access control).
4. `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `LRTOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` returns the clamped `minAnswer` value.
5. `totalETHInProtocol` is inflated by `(0.5 - 0.01) * totalStETHDeposits` ETH.
6. `newRsETHPrice` is set higher than the true backing value.
7. New depositors calling `LRTDepositPool.depositAsset()` receive fewer rsETH shares than the true asset value warrants, while existing holders are unjustly enriched. [6](#0-5) [7](#0-6)

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

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```
