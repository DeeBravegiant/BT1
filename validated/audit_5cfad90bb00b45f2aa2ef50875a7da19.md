Audit Report

## Title
Missing Staleness Validation in `ChainlinkPriceOracle.getAssetPrice()` Enables Deposit-Time Rate Manipulation — (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards the `updatedAt` timestamp and `answeredInRound` return values, accepting any price — including arbitrarily stale ones — as valid. This stale price propagates directly into the rsETH minting formula in `LRTDepositPool`, allowing an attacker to deposit LST assets at an inflated stale price and receive more rsETH than the assets are currently worth, diluting all existing rsETH holders.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches price data from Chainlink but only reads the `price` field, discarding all staleness indicators:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
``` [1](#0-0) 

The same codebase already demonstrates the correct pattern in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which validates both `answeredInRound < roundID` and `timestamp == 0`: [2](#0-1) 

The stale price flows through the following call chain:

1. `LRTDepositPool.depositAsset()` calls `_beforeDeposit()` → `getRsETHAmountToMint()`: [3](#0-2) 

2. `getRsETHAmountToMint()` computes `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`: [4](#0-3) 

3. `LRTOracle.getAssetPrice()` delegates to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)`, which resolves to `ChainlinkPriceOracle`: [5](#0-4) 

If `getAssetPrice(asset)` returns a stale price higher than the current market price, the depositor receives more rsETH than their assets are worth. The same stale price also feeds `_getTotalEthInProtocol()`, which is used by the public `updateRSETHPrice()` to update the global rsETH/ETH rate: [6](#0-5) [7](#0-6) 

The `pricePercentageLimit` guard in `_updateRsETHPrice()` only triggers on large price movements and does not prevent the deposit-time minting exploit, since the minting uses the live (stale) oracle price directly, not the stored `rsETHPrice`. [8](#0-7) 

## Impact Explanation
**Critical — Direct theft of user funds.**

When a Chainlink LST/ETH feed goes stale at a price above the current market (e.g., during a rapid market downturn, oracle node failure, or network congestion), an attacker deposits LST tokens and receives rsETH computed at the inflated stale price. The excess rsETH represents a claim on ETH value that was never deposited. When the attacker later redeems via `LRTWithdrawalManager`, they extract more ETH-equivalent value than they contributed, with the shortfall borne by all existing rsETH holders. This is direct, at-rest fund theft from protocol depositors.

## Likelihood Explanation
**Medium.** Chainlink feeds for LSTs (stETH/ETH, ETHx/ETH, sfrxETH/ETH) have historically experienced staleness during periods of high network congestion or oracle node issues. The attacker's entry path — `depositAsset()` on `LRTDepositPool` — is fully permissionless. The attacker only needs to monitor the on-chain `updatedAt` timestamp of the relevant Chainlink feed and act when it lags behind the current market price. No privileged access is required.

## Recommendation
Add staleness validation to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optionally enforce a maximum age, e.g.:
    // if (block.timestamp - updatedAt > MAX_STALENESS_SECONDS) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

## Proof of Concept
1. Deploy a mock Chainlink aggregator for a supported LST (e.g., stETH/ETH) that returns a stale `updatedAt` timestamp and a price 5% above the current market rate. Wire it into `ChainlinkPriceOracle` via `updatePriceFeedFor`.
2. Call `LRTDepositPool.depositAsset(stETH, amount, 0, "")` as an unprivileged depositor. The minting formula uses the stale inflated `getAssetPrice(stETH)`, issuing excess rsETH proportional to the price inflation.
3. Verify that `rsethAmountToMint` exceeds what would be minted at the true market price.
4. After the oracle updates and `updateRSETHPrice()` is called to normalize the rsETH price, initiate and complete withdrawal via `LRTWithdrawalManager` to redeem the excess rsETH for more ETH-equivalent value than was deposited.
5. Confirm the profit is extracted from the pool of existing rsETH holders, whose share of the underlying TVL is diluted.

**Foundry fork test outline:**
```solidity
function testStaleOracleDeposit() public {
    // 1. Fork mainnet, set up mock stale Chainlink feed at price * 1.05
    // 2. Record attacker rsETH balance before deposit
    // 3. depositAsset(stETH, 1e18, 0, "")
    // 4. Assert rsethMinted > getRsETHAmountToMint(stETH, 1e18) at true price
    // 5. updateRSETHPrice(), initiate + complete withdrawal
    // 6. Assert ETH received > 1e18 (profit at expense of other holders)
}
```

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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
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
