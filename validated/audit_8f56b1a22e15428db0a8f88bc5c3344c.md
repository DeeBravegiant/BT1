### Title
No Staleness Check on Chainlink `latestRoundData()` Allows Stale Price to Corrupt rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards every return value except `price`. No `updatedAt` timestamp check, no `answeredInRound >= roundId` check, and no heartbeat validation are performed. This stale price feeds directly into `LRTOracle._getTotalEthInProtocol()`, which determines the rsETH exchange rate used to mint rsETH for every depositor.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values from `latestRoundData()` — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — are available, but only `answer` is used. The fields `updatedAt` (timestamp of last update) and `answeredInRound` (round in which the answer was computed, used to detect incomplete rounds) are silently discarded. [1](#0-0) 

By contrast, the sibling contract `ChainlinkOracleForRSETHPoolCollateral` in the same repository explicitly validates both `answeredInRound < roundID` and `timestamp == 0`, demonstrating the project is aware of the pattern but failed to apply it here. [2](#0-1) 

The stale price returned by `ChainlinkPriceOracle.getAssetPrice()` is consumed by `LRTOracle.getAssetPrice()`, which delegates directly to the registered `IPriceFetcher`: [3](#0-2) 

`_getTotalEthInProtocol()` iterates over every supported LST asset and multiplies its total deposited balance by the (potentially stale) price to compute the protocol's total ETH value: [4](#0-3) 

This total ETH value is then used in `_updateRsETHPrice()` to compute `newRsETHPrice = totalETHInProtocol / rsethSupply`, which is stored as the authoritative exchange rate: [5](#0-4) 

`updateRSETHPrice()` is a **public, permissionless function** callable by any address: [6](#0-5) 

---

### Impact Explanation

When a Chainlink feed goes stale and returns a price lower than the true market value of an LST:

1. An attacker calls the public `updateRSETHPrice()`, pushing a deflated `rsETHPrice` into storage.
2. The attacker immediately calls `depositAsset()` with that LST. Because `rsETHPrice` is now artificially low, the protocol mints more rsETH per unit of deposited asset than it should.
3. The attacker holds excess rsETH representing a claim on more underlying ETH than they contributed, at the expense of all existing rsETH holders (their share of the pool is diluted).

This constitutes **theft of yield / value from existing rsETH holders**. The `pricePercentageLimit` guard provides partial mitigation only when it is set to a non-zero value and only for deviations exceeding the configured threshold; small or moderate staleness passes through silently. [7](#0-6) 

---

### Likelihood Explanation

Chainlink feeds can go stale due to network congestion, sequencer downtime (on L2), or feed deprecation. The `updateRSETHPrice()` function is public and requires no special role, so any depositor can trigger a price update at will. The attacker does not need to manipulate the oracle — they only need to act during a naturally occurring staleness window. LST/ETH feeds (stETH/ETH, rETH/ETH, etc.) have historically experienced brief staleness periods. Likelihood is **medium**.

---

### Recommendation

Add staleness and validity checks to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optional: if (block.timestamp - updatedAt > HEARTBEAT_TIMEOUT) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

This mirrors the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`. [2](#0-1) 

---

### Proof of Concept

1. Chainlink LST/ETH feed (e.g., stETH/ETH) becomes stale, returning a price 2% below the true rate.
2. Attacker observes the stale feed on-chain.
3. Attacker calls `LRTOracle.updateRSETHPrice()` — no role required. `_getTotalEthInProtocol()` uses the stale price, computing a total ETH value ~2% lower than reality. `rsETHPrice` is written to storage at the deflated value (assuming the drop is within `pricePercentageLimit`, or that `pricePercentageLimit == 0`).
4. Attacker calls `LRTDepositPool.depositAsset(stETH, amount, 0, "")`. The mint calculation uses the deflated `rsETHPrice`, issuing ~2% more rsETH than the deposited stETH is worth.
5. When the feed recovers and `updateRSETHPrice()` is called again, `rsETHPrice` rises back to the true value. The attacker's excess rsETH now represents a claim on more ETH than they deposited, extracted from existing holders.

Entry path: `depositAsset()` → `_beforeDeposit()` → `getRsETHAmountToMint()` (uses stored `rsETHPrice` set by the prior `updateRSETHPrice()` call) → attacker receives inflated rsETH. [8](#0-7) [1](#0-0)

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
