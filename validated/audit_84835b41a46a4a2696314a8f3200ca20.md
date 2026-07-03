### Title
Chainlink Price Staleness Indicators Ignored in `ChainlinkPriceOracle.getAssetPrice()` - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validity fields (`roundId`, `startedAt`, `updatedAt`, `answeredInRound`), accepting any price — including stale ones — without any staleness check. This is the direct Chainlink analog to ignoring Pyth's confidence interval. The same codebase applies proper staleness checks in `ChainlinkOracleForRSETHPoolCollateral`, confirming the omission is unintentional.

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values are available — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — but only `answer` is used. The fields `updatedAt` (timestamp of last update) and `answeredInRound` (round in which the answer was computed, used to detect incomplete rounds) are silently discarded. [1](#0-0) 

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repository correctly validates both fields:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L27-32
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();

if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The stale price returned by `ChainlinkPriceOracle.getAssetPrice()` propagates through the following call chain:

1. `LRTOracle.getAssetPrice(asset)` → delegates to `ChainlinkPriceOracle.getAssetPrice()` [3](#0-2) 

2. `LRTOracle._getTotalEthInProtocol()` → calls `getAssetPrice()` for every supported asset to compute total ETH value [4](#0-3) 

3. `LRTOracle._updateRsETHPrice()` → uses the stale total ETH to compute and store `rsETHPrice` [5](#0-4) 

4. `LRTDepositPool.getRsETHAmountToMint()` → uses `lrtOracle.getAssetPrice(asset)` and `lrtOracle.rsETHPrice()` to determine how many rsETH tokens a depositor receives [6](#0-5) 

### Impact Explanation

If a Chainlink feed becomes stale (e.g., during network congestion or a sequencer outage on L2), the protocol continues using the last reported price with no reversion or circuit-breaker. Two concrete outcomes:

- **Stale price lower than actual**: `_getTotalEthInProtocol()` underestimates TVL → `newRsETHPrice` is computed lower than it should be. If the drop exceeds `pricePercentageLimit`, `_updateRsETHPrice()` pauses both `LRTDepositPool` and `LRTWithdrawalManager`, temporarily freezing all user funds. [7](#0-6) 

- **Stale price higher than actual**: `_getTotalEthInProtocol()` overestimates TVL → `rsETHPrice` is set too high → depositors receive fewer rsETH tokens than they are entitled to, and withdrawers receive more assets than they should (the protocol delivers incorrect returns).

**Impact**: Medium — Temporary freezing of funds (stale-low scenario) / Low — Contract fails to deliver promised returns (stale-high scenario).

### Likelihood Explanation

Chainlink feeds can go stale during Ethereum network congestion, L2 sequencer downtime, or oracle node failures. These are real, historically observed events. `updateRSETHPrice()` is a public function callable by any unprivileged address, so any user can trigger the price update at the moment a stale price is live, locking it in before the oracle recovers. [8](#0-7) 

### Recommendation

Apply the same staleness checks already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Optionally, add a configurable `heartbeat` threshold check (`block.timestamp - updatedAt > heartbeat`) for feeds with known update frequencies.

### Proof of Concept

1. Chainlink's LST/ETH feed (e.g., stETH/ETH) stops updating due to network congestion. The last reported price is 10% below the true market price.
2. Attacker calls `LRTOracle.updateRSETHPrice()` (public, no access control).
3. `_getTotalEthInProtocol()` uses the stale 10%-low price → `newRsETHPrice` is computed ~10% below `highestRsethPrice`.
4. If `pricePercentageLimit` is set to, say, 5% (1% = 1e16), the condition `diff > pricePercentageLimit.mulWad(highestRsethPrice)` is true.
5. `LRTDepositPool.pause()` and `LRTWithdrawalManager.pause()` are called → all deposits and withdrawals are frozen until an admin manually unpauses. [9](#0-8)

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

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
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
