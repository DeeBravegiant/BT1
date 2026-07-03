### Title
`ChainlinkPriceOracle.getAssetPrice()` Accepts Stale/Invalid Chainlink Prices Without Validation - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validity fields (`roundId`, `updatedAt`, `answeredInRound`), making the same class of incorrect assumption as the reference report: that the returned price is always valid. A stale or zero price flows directly into rsETH minting and TVL accounting, enabling depositors to receive inflated rsETH or causing the rsETH price to be miscalculated.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values from `latestRoundData()` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. The implementation silently discards `roundId`, `updatedAt`, and `answeredInRound`, performing no checks for:
- **Staleness**: `updatedAt` is never compared to `block.timestamp`; a feed that has not updated in days is accepted as fresh.
- **Incomplete round**: `updatedAt == 0` (round not yet finalized) is not rejected.
- **Stale round**: `answeredInRound < roundId` is not rejected.
- **Non-positive price**: `price <= 0` is not rejected; `uint256(price)` would wrap a negative `int256` to a huge number.

The protocol's own `ChainlinkOracleForRSETHPoolCollateral.getRate()` performs all three of these checks (`answeredInRound < roundID`, `timestamp == 0`, `ethPrice <= 0`), demonstrating the team is aware of the requirement but omitted it from the primary oracle used for LST pricing. [1](#0-0) [2](#0-1) 

The stale price propagates through two critical paths:

**Path 1 – Deposit minting:**
`depositAsset()` → `_beforeDeposit()` → `getRsETHAmountToMint()` → `lrtOracle.getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice()`. [3](#0-2) 

**Path 2 – rsETH price update:**
`updateRSETHPrice()` → `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `getAssetPrice(asset)` for every supported LST. [4](#0-3) 

### Impact Explanation
**High – Theft of unclaimed yield.**

If a Chainlink LST/ETH feed (e.g., stETH/ETH) goes stale at a price higher than the real market price, any depositor calling `depositAsset()` receives more rsETH than the deposited LST is worth. The excess rsETH represents a claim on ETH that was not contributed, diluting the backing of all existing rsETH holders and constituting theft of their accrued yield.

Conversely, a stale low price fed into `_getTotalEthInProtocol()` artificially deflates the computed TVL, causing `_updateRsETHPrice()` to compute a lower `newRsETHPrice`. If the drop exceeds `pricePercentageLimit`, the protocol auto-pauses, temporarily freezing all deposits and withdrawals. [5](#0-4) 

### Likelihood Explanation
**Low.** Chainlink LST/ETH feeds on Ethereum mainnet are updated on a heartbeat (typically 24 h) and a deviation threshold (0.5%). However:
- Feeds can be deprecated without notice, leaving the last answer permanently stale.
- During extreme network congestion, keeper transactions may fail to land within the heartbeat window.
- A negative `int256` answer (theoretically possible if a feed malfunctions) would wrap to an astronomically large `uint256`, immediately corrupting the rsETH price.

No attacker action is required beyond waiting for a stale feed and then calling `depositAsset()`.

### Recommendation
Apply the same validation already present in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optional: if (block.timestamp - updatedAt > HEARTBEAT) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

### Proof of Concept
1. Chainlink stETH/ETH feed last updated at `T - 25h` (past heartbeat); `updatedAt` is stale but `latestRoundData()` still returns the old answer.
2. Real stETH/ETH market price has dropped from 0.9990 to 0.9900 ETH (a 0.9% drop, within normal range).
3. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, minRsETH, "")`.
4. `getRsETHAmountToMint()` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `0.9990e18` instead of real `0.9900e18`.
5. `rsethAmountToMint = (1000e18 * 0.9990e18) / rsETHPrice` — attacker receives ~0.9% more rsETH than the deposited stETH is worth.
6. Attacker immediately requests withdrawal, redeeming the excess rsETH for ETH that was contributed by other depositors. [1](#0-0) [6](#0-5) [7](#0-6)

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

**File:** contracts/LRTOracle.sol (L250-281)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

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

        // downside protection — pause if price drops too far
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
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
