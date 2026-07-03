### Title
`ChainlinkPriceOracle` Does Not Verify Price Staleness, Enabling Stale-Price Deposit Exploitation - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle::getAssetPrice()` calls Chainlink's `latestRoundData()` but discards all staleness indicators (`updatedAt`, `answeredInRound`, `roundId`). A stale price for any supported LST asset flows directly into `LRTDepositPool::getRsETHAmountToMint()`, allowing a depositor to mint rsETH at an incorrect exchange rate — either over-minting (stealing value from existing holders) or under-minting. The protocol already demonstrates awareness of this class of bug: `ChainlinkOracleForRSETHPoolCollateral` performs full staleness validation, but `ChainlinkPriceOracle` — the oracle used for all LST deposit pricing — does not.

---

### Finding Description

`ChainlinkPriceOracle::getAssetPrice()` fetches the Chainlink price as follows:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
``` [1](#0-0) 

The five return values of `latestRoundData()` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. The function destructures only `price` (the second value) and ignores `updatedAt` and `answeredInRound` entirely. No check is made for:
- `answeredInRound < roundId` (round not completed / stale)
- `updatedAt == 0` (incomplete round)
- `block.timestamp - updatedAt > threshold` (heartbeat staleness)
- `price <= 0` (invalid price)

By contrast, `ChainlinkOracleForRSETHPoolCollateral::getRate()` — used for pool collateral — performs all of these checks:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L26-37
function getRate() public view returns (uint256) {
    (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
        AggregatorV3Interface(oracle).latestRoundData();
    if (answeredInRound < roundID) revert StalePrice();
    if (timestamp == 0) revert IncompleteRound();
    if (ethPrice <= 0) revert InvalidPrice();
    ...
}
``` [2](#0-1) 

The stale price from `ChainlinkPriceOracle` propagates through `LRTOracle::getAssetPrice()`:

```solidity
// contracts/LRTOracle.sol L156-158
function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
    return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
}
``` [3](#0-2) 

And then into `LRTDepositPool::getRsETHAmountToMint()`, which determines how many rsETH tokens a depositor receives:

```solidity
// contracts/LRTDepositPool.sol L519-521
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [4](#0-3) 

The stale price also feeds `_getTotalEthInProtocol()` in `LRTOracle`, which is used to compute the rsETH/ETH exchange rate during `updateRSETHPrice()`:

```solidity
// contracts/LRTOracle.sol L339
uint256 assetER = getAssetPrice(asset);
``` [5](#0-4) 

---

### Impact Explanation

**Critical — Direct theft of user funds.**

If a Chainlink feed for a supported LST (e.g., stETH, rETH, ETHx) becomes stale and the last reported price is higher than the current market price (e.g., during an LST depeg event or oracle downtime), an attacker can:

1. Deposit the depegged LST at the inflated stale price.
2. Receive more rsETH than the deposited LST is actually worth.
3. Redeem or sell the excess rsETH, extracting value from existing rsETH holders.

The inverse (stale price lower than market) causes depositors to receive fewer rsETH than deserved, but the primary theft vector is the inflated-price case. The `rsETHPrice` itself is also computed using stale LST prices via `_getTotalEthInProtocol()`, compounding the miscalculation.

---

### Likelihood Explanation

Chainlink feeds have documented heartbeat intervals (e.g., 1 hour for ETH/USD, 24 hours for some LST feeds). During periods of network congestion, oracle node downtime, or rapid market movement, feeds can lag significantly. LST depeg events (e.g., stETH during the 2022 Merge period) are historically documented. Any such event creates a window where this vulnerability is exploitable by any unprivileged depositor calling `depositAsset()`.

---

### Recommendation

Add staleness validation in `ChainlinkPriceOracle::getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > STALENESS_THRESHOLD) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`STALENESS_THRESHOLD` should be set per-asset based on the Chainlink feed's documented heartbeat interval.

---

### Proof of Concept

**Entry path (unprivileged depositor):**

1. Chainlink LST/ETH feed goes stale; last reported price is 1.05e18 but actual market price is 0.95e18 (LST depeg).
2. Attacker calls `LRTDepositPool::depositAsset(lstAddress, 100e18, 0, "")`.
3. `_beforeDeposit()` → `getRsETHAmountToMint()` → `lrtOracle.getAssetPrice(lstAddress)` → `ChainlinkPriceOracle::getAssetPrice()` → returns stale `1.05e18`.
4. `rsethAmountToMint = (100e18 * 1.05e18) / rsETHPrice` — attacker receives ~10.5% more rsETH than the deposited LST is worth.
5. Attacker redeems or sells the excess rsETH, extracting value from existing holders.

The root cause is exclusively in `contracts/oracles/ChainlinkPriceOracle.sol` line 52, where `updatedAt` and `answeredInRound` are silently discarded. [6](#0-5)

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

**File:** contracts/LRTOracle.sol (L336-344)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
