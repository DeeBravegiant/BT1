### Title
Missing Chainlink Price Feed Validation Allows Stale Prices to Corrupt rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` without validating staleness, round completeness, or price sign. This stale price flows directly into `LRTDepositPool`'s rsETH minting calculation, allowing depositors to receive more (or fewer) rsETH tokens than the true ETH value of their deposit.

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` retrieves the Chainlink price but discards all validation fields:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`roundId`, `startedAt`, `updatedAt`, and `answeredInRound` are all silently ignored. There is no:
- Staleness check (`block.timestamp - updatedAt > heartbeat`)
- Round completeness check (`answeredInRound >= roundId`)
- Positive price check (`price > 0`)

This contrasts directly with `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which performs all three checks in the same codebase:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L27-32
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The unvalidated price propagates through the following call chain to every public deposit:

1. `LRTDepositPool.depositAsset()` / `depositETH()` [1](#0-0) 
2. → `_beforeDeposit()` → `getRsETHAmountToMint()` [2](#0-1) 
3. → `LRTOracle.getAssetPrice()` → `ChainlinkPriceOracle.getAssetPrice()` [3](#0-2) 
4. → `latestRoundData()` with no validation [4](#0-3) 

### Impact Explanation

**Impact: Medium**

The rsETH minting formula is:
```
rsethAmountToMint = (depositAmount × assetPrice) / rsETHPrice
```

If the Chainlink feed for a supported LST (e.g., stETH, rETH, ETHx) is stale and its reported price is **higher** than the true market price (e.g., feed shows 1.05 ETH/stETH but real price is 1.02 ETH/stETH after a depeg event), a depositor receives more rsETH than the actual ETH value they contributed. This dilutes all existing rsETH holders — a form of theft of unclaimed yield / share mis-accounting. Conversely, a stale low price causes the contract to fail to deliver promised returns to the depositor.

### Likelihood Explanation

**Likelihood: Medium**

Chainlink feeds have documented heartbeat intervals (e.g., 1 hour for ETH/stETH on mainnet). During periods of network congestion, L2 sequencer downtime, or extreme market volatility, feeds can lag significantly behind real prices. The protocol supports multiple LST assets each with its own feed, multiplying the surface area. No off-chain keeper or on-chain guard prevents deposits from proceeding with a stale price.

### Recommendation

Add staleness and validity checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_STALENESS` should be set per-feed based on the Chainlink heartbeat (e.g., 3600 seconds for a 1-hour heartbeat feed).

### Proof of Concept

1. Chainlink stETH/ETH feed last updated 2 hours ago at price `1.05e18` (real price has since dropped to `1.02e18` due to a depeg event).
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
3. `getRsETHAmountToMint` computes: `(100e18 × 1.05e18) / rsETHPrice` → attacker receives ~3% more rsETH than the true ETH value deposited.
4. Attacker immediately requests withdrawal, extracting value from existing rsETH holders.
5. No revert occurs anywhere in the path because `ChainlinkPriceOracle` never checks `updatedAt`. [4](#0-3) [5](#0-4) [2](#0-1)

### Citations

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

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
