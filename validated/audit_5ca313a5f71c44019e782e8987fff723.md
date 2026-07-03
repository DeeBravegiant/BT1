### Title
Missing Stale Price Check in `ChainlinkPriceOracle.getAssetPrice()` Allows Stale Prices to Drive rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls Chainlink's `latestRoundData()` but discards all return values except `price`, performing no staleness validation. This stale price is consumed directly by `LRTDepositPool.getRsETHAmountToMint()` to determine how many rsETH tokens to mint for a depositor, enabling incorrect minting when a Chainlink feed stops updating.

### Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol`, the `getAssetPrice()` function fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All five return values of `latestRoundData()` — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — are available, but only `answer` (aliased as `price`) is used. The contract performs no check on:
- `updatedAt` vs `block.timestamp` (time-based staleness threshold)
- `answeredInRound < roundId` (round-based staleness)
- `updatedAt == 0` (incomplete round)
- `price <= 0` (invalid/negative price)

This is in direct contrast to `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`, which does implement partial staleness checks (`answeredInRound < roundID`, `timestamp == 0`, `ethPrice <= 0`), demonstrating the protocol is aware of the requirement but failed to apply it consistently. [2](#0-1) 

The stale price from `ChainlinkPriceOracle.getAssetPrice()` flows into `LRTOracle.getAssetPrice()`: [3](#0-2) 

Which is then used in `LRTDepositPool.getRsETHAmountToMint()` to compute the rsETH minting amount: [4](#0-3) 

### Impact Explanation
If a Chainlink price feed for a supported LST (e.g., stETH/ETH) becomes stale with a price lower than the true current price (e.g., the feed froze during a temporary depeg that has since recovered), a depositor calling `depositAsset()` will receive more rsETH than the deposited collateral is worth. This dilutes the backing of all existing rsETH holders, constituting a share/asset mis-accounting that trends toward protocol insolvency. Conversely, a stale price higher than reality causes depositors to receive fewer rsETH tokens than owed — a failure to deliver promised returns.

**Impact: Low to Critical** — "Contract fails to deliver promised returns" at minimum; protocol insolvency if stale-low prices are exploited by a depositor. [5](#0-4) 

### Likelihood Explanation
Chainlink feeds can go stale due to oracle node downtime, network congestion, or sequencer issues (especially relevant on L2 deployments). The `ChainlinkPriceOracle` is the live production oracle for all supported LST assets (stETH, rETH, etc.) as confirmed by the mainnet deployment table. Any depositor can trigger this path permissionlessly at any time a feed is stale. [6](#0-5) 

### Recommendation
Apply the same staleness checks already present in `ChainlinkOracleForRSETHPoolCollateral` and add a time-based threshold check in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > STALE_PRICE_THRESHOLD) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`STALE_PRICE_THRESHOLD` should be set per feed based on its heartbeat (e.g., 1 hour for ETH/USD, 24 hours for LST/ETH feeds).

### Proof of Concept
1. Chainlink stETH/ETH feed stops updating (e.g., last `updatedAt` is 2 hours ago, feed heartbeat is 1 hour). The stale price is `0.9995e18` (ETH) while the true price has moved to `1.0005e18`.
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, minRSETH, "")`.
3. `_beforeDeposit` → `getRsETHAmountToMint(stETH, 1000e18)` → `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `0.9995e18` (stale).
4. `rsethAmountToMint = (1000e18 * 0.9995e18) / rsETHPrice` — if rsETHPrice also reflects `1.0005e18`, the attacker receives more rsETH than the 1000 stETH deposited is worth at the true rate.
5. The attacker redeems the over-minted rsETH, extracting value from the protocol's backing pool. [7](#0-6)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-33)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTDepositPool.sol (L99-117)
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
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L648-669)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
