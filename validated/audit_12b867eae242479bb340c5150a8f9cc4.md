### Title
Chainlink Oracle Output Not Validated for Staleness or Zero Price, Corrupting rsETH Price and Enabling Fund Loss - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` without checking whether the returned price is zero, negative, or stale. This raw price feeds directly into `LRTOracle._updateRsETHPrice()` via the public `updateRSETHPrice()` entry point, allowing any unprivileged caller to corrupt the protocol-wide `rsETHPrice` using a degraded Chainlink feed.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price with no validity checks:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

The five return values of `latestRoundData()` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. The code discards `updatedAt`, `answeredInRound`, and `roundId` entirely, so:
- A stale price (feed not updated for hours/days) is accepted as current.
- A zero or negative `price` is silently cast to `uint256(0)` or wraps, producing a nonsensical exchange rate.

Contrast this with the sibling contract in the same repository, `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which correctly validates all three conditions:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L27-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The unvalidated price propagates through the following call chain:

1. `ChainlinkPriceOracle.getAssetPrice(asset)` → returns bad price
2. `LRTOracle.getAssetPrice(asset)` (L157) → delegates to the above
3. `LRTOracle._getTotalEthInProtocol()` (L339) → sums `totalAssetAmt * assetER` for all supported LSTs
4. `LRTOracle._updateRsETHPrice()` (L231, L250) → computes `newRsETHPrice = totalETHInProtocol / rsethSupply`
5. `LRTOracle.updateRSETHPrice()` (L87) → **public, callable by anyone**

Additionally, `LRTDepositPool.getRsETHAmountToMint()` (L520) reads `lrtOracle.getAssetPrice(asset)` live at deposit time, so a stale/zero price also directly distorts how many rsETH tokens a depositor receives.

### Impact Explanation
**Scenario A — Zero price (High: Theft of unclaimed yield / dilution)**
If a Chainlink feed for a major LST (e.g., stETH, ETHx) returns `price = 0` during an oracle outage, `totalETHInProtocol` is massively understated. Any caller triggers `updateRSETHPrice()`, setting `rsETHPrice` far below its true value. A depositor who then calls `depositAsset()` or `depositETH()` receives `(amount * assetPrice) / rsETHPrice` rsETH — with a deflated denominator, they receive far more rsETH than their deposit is worth, diluting all existing rsETH holders. This constitutes theft of unclaimed yield from existing holders.

**Scenario B — Stale high price (Medium: Temporary freeze)**
If a feed goes stale while holding a price higher than the current market (e.g., during a depeg), `totalETHInProtocol` is overstated, `rsETHPrice` is inflated. When the true price eventually updates and `updateRSETHPrice()` is called, the computed price may drop below `highestRsethPrice` by more than `pricePercentageLimit`, triggering the automatic pause of `LRTDepositPool` and `LRTWithdrawalManager`, temporarily freezing all user funds.

### Likelihood Explanation
Chainlink feeds have historically returned stale data or zero prices during network congestion, sequencer downtime (on L2), or feed deprecation. `updateRSETHPrice()` is permissionless — any external actor can call it at any time. The combination of a degraded feed and a public price-update function makes this exploitable without any privileged access.

### Recommendation
Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > MAX_STALENESS_PERIOD) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

### Proof of Concept

1. Assume stETH is a supported asset with `ChainlinkPriceOracle` as its price oracle.
2. The stETH/ETH Chainlink feed enters an outage and returns `price = 0`.
3. Attacker calls `LRTOracle.updateRSETHPrice()` (public, no access control).
4. Inside `_getTotalEthInProtocol()`, `getAssetPrice(stETH)` returns `0`, so stETH's entire TVL contributes `0` to `totalETHInProtocol`.
5. `newRsETHPrice = (understated_totalETH) / rsethSupply` — far below the true price.
6. If the drop is within `pricePercentageLimit`, `rsETHPrice` is updated to this deflated value.
7. Attacker immediately calls `depositETH()` or `depositAsset()`. `getRsETHAmountToMint` computes `(amount * assetPrice) / deflated_rsETHPrice`, minting far more rsETH than the deposit is worth.
8. All existing rsETH holders are diluted; the attacker has extracted value equivalent to the difference between the true and deflated rsETH price across their deposit amount.

**Key references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
