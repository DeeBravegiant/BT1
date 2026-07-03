### Title
Stale Chainlink Price Accepted Without Staleness Validation Enables Deposit-Time Rate Manipulation — (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards the `updatedAt` timestamp and `answeredInRound` values, accepting any price — including arbitrarily stale ones — as valid. This stale price propagates directly into the rsETH minting formula used by `LRTDepositPool`, allowing an attacker to deposit LST assets at an inflated stale price and receive more rsETH than the assets are currently worth, diluting all existing rsETH holders.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches price data from Chainlink but only reads the `price` field:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-L55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

The `updatedAt` and `answeredInRound` return values are completely ignored. [1](#0-0) 

The same codebase already demonstrates the correct pattern in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which validates both `answeredInRound < roundID` and `timestamp == 0`: [2](#0-1) 

The stale price flows through the following call chain into the deposit minting formula:

1. `LRTDepositPool.depositAsset()` / `depositETH()` → `_beforeDeposit()` → `getRsETHAmountToMint()` [3](#0-2) 
2. `getRsETHAmountToMint()` calls `lrtOracle.getAssetPrice(asset)` and `lrtOracle.rsETHPrice()`: [4](#0-3) 
3. `LRTOracle.getAssetPrice()` delegates to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)`, which resolves to `ChainlinkPriceOracle`: [5](#0-4) 

The minting formula is:
```
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
If `getAssetPrice(asset)` returns a stale price that is higher than the current market price, the depositor receives more rsETH than their assets are worth. [6](#0-5) 

The same stale price also feeds `LRTOracle._getTotalEthInProtocol()`, which is used by the public `updateRSETHPrice()` function — callable by anyone — to update the global rsETH/ETH rate, compounding the exposure. [7](#0-6) 

---

### Impact Explanation

**Critical — Direct theft of user funds.**

When a Chainlink LST/ETH feed goes stale at a price above the current market (e.g., during a rapid market downturn, oracle node failure, or network congestion), an attacker deposits LST tokens and receives rsETH computed at the inflated stale price. The excess rsETH represents a claim on ETH value that was never deposited. When the attacker later redeems via `LRTWithdrawalManager`, they extract more ETH-equivalent value than they contributed, with the shortfall borne by all existing rsETH holders. This is direct, at-rest fund theft from protocol depositors.

---

### Likelihood Explanation

**Medium.** Chainlink feeds for LSTs (stETH/ETH, ETHx/ETH, sfrxETH/ETH) have historically experienced staleness during periods of high network congestion or oracle node issues. The attacker's entry path — `depositAsset()` on `LRTDepositPool` — is fully permissionless. The attacker only needs to monitor the on-chain `updatedAt` timestamp of the relevant Chainlink feed and act when it lags behind the current market price. No privileged access is required.

---

### Recommendation

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

---

### Proof of Concept

1. Monitor the on-chain Chainlink feed for a supported LST (e.g., stETH/ETH). Observe `updatedAt` and the current reported price.
2. Wait for a market event (rapid price drop) where the Chainlink feed has not yet updated — the stale price is higher than the true market price.
3. Call `LRTDepositPool.depositAsset(stETH, amount, 0, "")` as an unprivileged depositor. The minting formula uses the stale inflated `getAssetPrice(stETH)`, issuing excess rsETH.
4. Hold the excess rsETH. After the oracle updates and the rsETH price stabilizes, call `LRTWithdrawalManager.initiateWithdrawal()` and `completeWithdrawal()` to redeem the rsETH for more ETH-equivalent value than was deposited.
5. The profit is extracted from the pool of existing rsETH holders, whose share of the underlying TVL is diluted.

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

**File:** contracts/LRTDepositPool.sol (L511-521)
```text
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
