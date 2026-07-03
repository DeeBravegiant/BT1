### Title
Missing Chainlink Staleness Checks Allow Stale Price Acceptance in `getAssetPrice` - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `answer`, performing no staleness validation. A stale price flows directly into rsETH minting calculations and rsETH price updates, enabling depositors to mint excess rsETH or causing incorrect protocol-wide price updates.

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

The `updatedAt` timestamp, `startedAt`, and `roundId`/`answeredInRound` values are all silently discarded. There is no check that:
- `updatedAt + GRACE_PERIOD > block.timestamp` (staleness)
- `startedAt != 0` (incomplete round)
- `price > 0` (valid answer)

This price is consumed by `LRTOracle.getAssetPrice()`: [2](#0-1) 

Which is called inside `_getTotalEthInProtocol()` for every supported asset: [3](#0-2) 

The computed total ETH drives `_updateRsETHPrice()`, which is publicly callable via `updateRSETHPrice()`: [4](#0-3) 

The stale price also directly feeds `LRTDepositPool.getRsETHAmountToMint()`: [5](#0-4) 

Which is called on every user deposit via `depositAsset()` and `depositETH()`: [6](#0-5) 

### Impact Explanation

**Critical — Protocol insolvency / direct theft of funds.**

If a Chainlink feed goes stale with an inflated price (e.g., the feed freezes during a sharp market decline), a depositor calling `depositAsset()` receives rsETH computed as:

```
rsethAmountToMint = (depositAmount × stalePriceHigh) / rsETHPrice
```

The depositor receives more rsETH than their deposit is worth in real terms. When they later redeem, they extract more ETH than they deposited, at the expense of other protocol participants. With multiple supported LST assets each having independent Chainlink feeds, the attack surface is multiplied.

Additionally, `updateRSETHPrice()` is permissionless — any caller can trigger a global rsETH price update using stale asset prices, causing the stored `rsETHPrice` to diverge from reality, compounding the minting miscalculation.

### Likelihood Explanation

Chainlink feeds do go stale: during network congestion, gas price spikes, or Chainlink node outages, the heartbeat update can be missed for hours. The ETH mainnet deployment uses multiple LST feeds (stETH/ETH, rETH/ETH, etc.), each independently susceptible. No special attacker capability is required — a depositor simply monitors for a stale feed and calls `depositAsset()` during the staleness window.

### Recommendation

Add staleness and validity checks in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
uint256 private constant GRACE_PERIOD = 3600; // 1 hour; tune per feed heartbeat

function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    require(price > 0, "Invalid price");
    require(startedAt != 0, "Incomplete round");
    require(answeredInRound >= roundId, "Stale round");
    require(block.timestamp <= updatedAt + GRACE_PERIOD, "Stale price");

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Consider storing per-feed heartbeat grace periods in a mapping (as recommended in the original report) since different Chainlink feeds have different update frequencies.

### Proof of Concept

1. Chainlink stETH/ETH feed last updated at `T`. At `T + 2h`, the feed is stale but `latestRoundData()` still returns the old (higher) price.
2. Attacker calls `LRTDepositPool.depositAsset(stETH, amount, 0, "")`.
3. `getRsETHAmountToMint` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale inflated price with no revert.
4. `rsethAmountToMint = (amount × stalePriceHigh) / rsETHPrice` — attacker receives excess rsETH.
5. Attacker redeems rsETH for more ETH than deposited, draining value from honest depositors.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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

**File:** contracts/LRTOracle.sol (L336-343)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
