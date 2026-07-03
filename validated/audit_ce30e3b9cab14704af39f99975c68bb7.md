### Title
No Staleness Validation on Chainlink Price Feed Allows Stale Prices to Silently Corrupt rsETH Minting and Price Updates - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards every return value except `price`. There is no check on `updatedAt`, `answeredInRound`, or price sign. A stale or frozen Chainlink feed for any supported LST asset (stETH, rETH, ETHx, etc.) silently propagates an incorrect price into rsETH minting and rsETH price updates, causing depositors to receive wrong amounts of rsETH and corrupting the protocol-wide exchange rate.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` is the price source for all LST assets in the Kelp protocol:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();   // updatedAt, answeredInRound silently dropped
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
``` [1](#0-0) 

`latestRoundData()` returns `(roundId, answer, startedAt, updatedAt, answeredInRound)`. The contract ignores `updatedAt` (timestamp of last update) and `answeredInRound` (round in which the answer was computed). Neither a time-based staleness threshold nor a round-completeness check is applied.

This contrasts with `ChainlinkOracleForRSETHPoolCollateral.sol`, which at least checks `answeredInRound < roundID` and `timestamp == 0`: [2](#0-1) 

`ChainlinkPriceOracle.getAssetPrice()` is consumed at two critical points:

1. **`LRTOracle._getTotalEthInProtocol()`** — iterates all supported assets, prices each one via `getAssetPrice`, and sums the total ETH value. This total directly determines the new rsETH price in `_updateRsETHPrice()`. [3](#0-2) 

2. **`LRTDepositPool.getRsETHAmountToMint()`** — uses `lrtOracle.getAssetPrice(asset)` to compute how many rsETH tokens a depositor receives. [4](#0-3) 

---

### Impact Explanation

**Incorrect rsETH minting (share mis-accounting):** If a Chainlink feed for an LST (e.g., stETH/ETH) becomes stale and its last reported price is higher than the true current price, every call to `depositAsset()` or `depositETH()` mints more rsETH than the deposited value warrants. This dilutes existing rsETH holders — equivalent to partial theft of their yield/principal. Conversely, a stale low price causes depositors to receive fewer rsETH tokens than owed.

**Corrupted protocol-wide rsETH price:** `updateRSETHPrice()` is a public function callable by anyone. If called while a feed is stale, `rsETHPrice` is set to an incorrect value. This incorrect price then governs all subsequent minting ratios and withdrawal valuations until the next update.

Impact classification: **Medium — Temporary freezing / mis-accounting of funds**, with potential escalation to **High** (theft of unclaimed yield / dilution of existing holders) if the stale price diverges materially from the true price.

---

### Likelihood Explanation

Chainlink feeds for LST/ETH pairs (stETH/ETH, rETH/ETH, ETHx/ETH) have historically paused or lagged during periods of high network congestion or oracle node issues. The `updateRSETHPrice()` entry point is public and permissionless, meaning any external caller can trigger a price update at any time, including during a stale feed window. No off-chain keeper exclusivity or on-chain guard prevents this.

---

### Recommendation

Add staleness and round-completeness validation inside `ChainlinkPriceOracle.getAssetPrice()`, consistent with the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (price <= 0) revert InvalidPrice();
    if (answeredInRound < roundId) revert StalePrice();
    if (block.timestamp - updatedAt > STALENESS_THRESHOLD) revert StalePriceFeed();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`STALENESS_THRESHOLD` should be set per-asset based on the Chainlink feed's documented heartbeat (e.g., 86 400 s for daily-updated feeds, 3 600 s for hourly feeds).

---

### Proof of Concept

1. Chainlink's stETH/ETH feed stops updating (e.g., network congestion). Last reported price: `1.05e18` (stETH at a premium). True current price: `0.98e18` (stETH at a discount after a slashing event).
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0)`.
3. `getRsETHAmountToMint` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `1.05e18` with no revert.
4. Attacker receives `1000 * 1.05 / rsETHPrice` rsETH instead of `1000 * 0.98 / rsETHPrice` rsETH — approximately 7% more rsETH than the deposited value justifies.
5. Attacker redeems rsETH after the feed recovers, extracting value from existing holders.

The entry path `depositAsset()` → `getRsETHAmountToMint()` → `LRTOracle.getAssetPrice()` → `ChainlinkPriceOracle.getAssetPrice()` is fully permissionless and requires no special role. [1](#0-0) [5](#0-4) [6](#0-5)

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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
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
