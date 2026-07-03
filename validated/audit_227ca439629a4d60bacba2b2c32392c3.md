### Title
Stale Chainlink Price Accepted Without Freshness Validation in `getAssetPrice` - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards every return value except `price`. No `updatedAt` heartbeat check, no `answeredInRound >= roundId` check, and no zero/negative price guard are performed. A stale or frozen Chainlink price is silently accepted as current and flows directly into rsETH mint calculations in `LRTDepositPool`.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values of `latestRoundData()` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. The implementation binds only `answer` (`price`) and ignores the remaining four. Specifically:

- `updatedAt` — the timestamp of the last price update — is never compared against `block.timestamp`. There is no maximum-age threshold (e.g., `require(block.timestamp - updatedAt <= MAX_DELAY)`).
- `answeredInRound` is never compared against `roundId`, so an incomplete or in-progress round is not detected.
- `price` is never checked to be `> 0`, so a zero or negative answer is silently cast to a large `uint256` via two's complement. [1](#0-0) 

This price is returned through `LRTOracle.getAssetPrice()`: [2](#0-1) 

And consumed directly in `LRTDepositPool.getRsETHAmountToMint()` to determine how many rsETH tokens to mint per deposited LST: [3](#0-2) 

Which is called from the public `depositAsset()` entry point: [4](#0-3) 

Contrast this with `ChainlinkOracleForRSETHPoolCollateral`, which at least checks `answeredInRound < roundID`, `timestamp == 0`, and `ethPrice <= 0` — but even that contract omits a `block.timestamp - timestamp` heartbeat check. `ChainlinkPriceOracle` has none of these guards at all. [5](#0-4) 

---

### Impact Explanation

**High — Theft of unclaimed yield / protocol insolvency.**

If a supported LST's Chainlink feed goes stale at an inflated price (e.g., stETH feed freezes at 1.0 ETH/stETH during a depeg to 0.9 ETH), a depositor calling `depositAsset(stETH, amount, ...)` receives rsETH calculated at the stale 1.0 rate. The depositor immediately holds rsETH worth more than the ETH value they contributed, diluting all existing rsETH holders. Repeated deposits during the stale window drain value from the protocol's TVL, constituting theft of yield and, at scale, insolvency.

Conversely, if the stale price is lower than the true price, the depositor receives fewer rsETH than deserved — but the primary attack vector is the inflated-price direction.

---

### Likelihood Explanation

Chainlink feeds can go stale during:
- Network congestion preventing keeper transactions.
- A depeg or rapid price movement that causes the feed to lag.
- Chainlink node outages.

These are realistic, historically observed conditions. Any unprivileged depositor can exploit the window by simply calling `depositAsset` while the feed is stale — no special access or front-running is required.

---

### Recommendation

Add staleness and validity checks in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price, , uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    require(price > 0, "Invalid price");
    require(answeredInRound >= roundId, "Stale round");
    require(updatedAt != 0, "Incomplete round");
    require(block.timestamp - updatedAt <= MAX_PRICE_AGE, "Price too stale");

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_PRICE_AGE` should be set per-feed based on the feed's documented heartbeat (e.g., 3600 seconds for a 1-hour heartbeat feed). Apply the same pattern to `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which also lacks a heartbeat check.

---

### Proof of Concept

1. Assume stETH/ETH Chainlink feed is configured in `ChainlinkPriceOracle` and its last update was 4 hours ago at price `1.0e18` (1 stETH = 1 ETH).
2. The true market price of stETH has since dropped to `0.9e18` due to a depeg, but the feed has not updated.
3. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, minRSETH, "")`.
4. `getRsETHAmountToMint` computes: `rsethAmountToMint = (100e18 * 1.0e18) / rsETHPrice` using the stale price.
5. At true price the attacker should receive rsETH worth `90 ETH`; instead they receive rsETH worth `100 ETH` — a `~11%` overmint at the expense of existing holders.
6. No freshness check in `ChainlinkPriceOracle.getAssetPrice()` (line 52) prevents this. [1](#0-0) [6](#0-5)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
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
