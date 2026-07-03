### Title
Chainlink Oracle Price Feed Used Without Staleness Check Allows Stale Price to Inflate rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `price`, performing no staleness validation. This stale price propagates directly into `LRTDepositPool.depositAsset()`, allowing any depositor to mint rsETH at an incorrect exchange rate.

### Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol`, the `getAssetPrice` function fetches the Chainlink price but silently drops `updatedAt` and `answeredInRound`:

```solidity
// ChainlinkPriceOracle.sol L52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No check is made against `block.timestamp - updatedAt` or `answeredInRound < roundId`. This is in direct contrast to `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`, which does validate `answeredInRound < roundID` and `timestamp == 0`.

The stale price flows through the following call chain:

1. `ChainlinkPriceOracle.getAssetPrice(asset)` [1](#0-0) 
2. → `LRTOracle.getAssetPrice(asset)` [2](#0-1) 
3. → `LRTOracle._getTotalEthInProtocol()` (line 339) and `LRTDepositPool.getRsETHAmountToMint()` [3](#0-2) 
4. → `LRTDepositPool._beforeDeposit()` → `depositAsset()` / `depositETH()` [4](#0-3) 

### Impact Explanation
**High — Theft of unclaimed yield from existing rsETH holders.**

When a Chainlink feed goes stale (e.g., during network congestion or oracle heartbeat gap), the last reported price remains frozen. If the real market price of an LST (e.g., stETH) has dropped but the oracle still reports the old higher price, a depositor calling `depositAsset()` receives rsETH calculated at the inflated rate:

```
rsethAmountToMint = (depositAmount * stalePriceHigh) / rsETHPrice
```

The depositor receives more rsETH than the deposited assets are worth, diluting the share value of all existing rsETH holders. This constitutes direct theft of yield accrued by existing holders. Additionally, `updateRSETHPrice()` is publicly callable and uses the same stale price to compute `_getTotalEthInProtocol()`, which can cause incorrect protocol fee minting. [5](#0-4) 

### Likelihood Explanation
**Medium.** Chainlink feeds have a heartbeat (e.g., 1 hour for ETH/USD, 24 hours for some LST feeds) and a deviation threshold. During periods of low volatility or network congestion, feeds can remain stale for the full heartbeat window. The attack requires no special permissions — any address can call `depositAsset()` or `depositETH()`. The attacker only needs to observe that the oracle price is stale relative to the real market price and act within the staleness window.

### Recommendation
Add staleness and validity checks in `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    require(answeredInRound >= roundId, "Stale price: answeredInRound < roundId");
    require(updatedAt != 0, "Incomplete round");
    require(price > 0, "Invalid price");
    require(block.timestamp - updatedAt <= STALENESS_THRESHOLD, "Stale price: updatedAt too old");

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`STALENESS_THRESHOLD` should be set per-asset based on the Chainlink feed's documented heartbeat interval.

### Proof of Concept

1. Assume stETH/ETH Chainlink feed has a 24-hour heartbeat. At T=0, the feed reports `1.05 ETH` per stETH. At T=12h, stETH depegs to `0.95 ETH` on the market, but the oracle has not updated (no deviation threshold crossed).
2. Attacker observes the stale oracle still reports `1.05 ETH`.
3. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
4. `getRsETHAmountToMint` computes: `(1000e18 * 1.05e18) / rsETHPrice` → attacker receives rsETH worth `1050 ETH` in protocol accounting, while only depositing assets worth `950 ETH` at market.
5. The 100 ETH difference is extracted from existing rsETH holders' share value.
6. Attacker redeems rsETH via the withdrawal path, extracting more ETH than deposited. [1](#0-0) [6](#0-5) [7](#0-6)

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
