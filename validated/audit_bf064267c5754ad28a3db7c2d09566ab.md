### Title
Chainlink Price Oracle Lacks Staleness and Validity Checks, Enabling Stale Price Exploitation - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `price`, performing no staleness check (`updatedAt`), no round completeness check (`answeredInRound >= roundId`), and no validity check (`price > 0`). This is the direct analog of the PushOracle report: a price feed used for rsETH minting and TVL accounting can silently return a stale or invalid price.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values of `latestRoundData()` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. The implementation discards `roundId`, `startedAt`, `updatedAt`, and `answeredInRound` entirely. No check is made that:
- `updatedAt` is recent (no heartbeat/staleness window)
- `answeredInRound >= roundId` (round completeness)
- `price > 0` (valid answer)

By contrast, the sister contract `ChainlinkOracleForRSETHPoolCollateral` in the same repository explicitly performs all three checks:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

`ChainlinkPriceOracle` is registered as the `assetPriceOracle` for supported LST assets (stETH, ETHx, etc.) in `LRTOracle`. `LRTOracle.getAssetPrice()` delegates directly to it, and this price is consumed in two critical paths:

1. **rsETH minting** — `LRTDepositPool.getRsETHAmountToMint()` computes `(amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`, which determines how many rsETH tokens a depositor receives.
2. **TVL / rsETH price update** — `LRTOracle._getTotalEthInProtocol()` sums `totalAssetAmt.mulWad(assetER)` for all supported assets using the same stale price, then `_updateRsETHPrice()` uses this TVL to set the global `rsETHPrice`.

### Impact Explanation
If a Chainlink feed goes stale (e.g., sequencer downtime, feed deprecation, extreme market volatility causing circuit-breaker freezes), the last recorded price is returned indefinitely. A depositor who observes that the on-chain price is stale and diverges from the true market price can:

- **Over-mint rsETH**: If the stale price is higher than the true price, depositing an asset yields more rsETH than its true ETH value, diluting existing holders (theft of yield / protocol insolvency path).
- **Under-mint rsETH**: If the stale price is lower, the depositor receives fewer rsETH than deserved (contract fails to deliver promised returns).

Additionally, a `price` of `0` or negative (possible during a Chainlink incident) cast to `uint256` produces `0` or a massive wraparound value, causing division-by-zero or extreme over-minting.

**Impact**: High — theft of unclaimed yield / share mis-accounting; in the zero-price case, potential critical over-mint.

### Likelihood Explanation
Chainlink feeds do go stale during network congestion, sequencer outages (L2), or feed migrations. The absence of any heartbeat check means the window of exploitability is open for the entire duration of the staleness event. Any unprivileged depositor can call `depositAsset()` or `depositETH()` at any time, making this externally reachable with no special permissions.

### Recommendation
Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral`:

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

`STALENESS_THRESHOLD` should be configurable per asset (different feeds have different heartbeat intervals).

### Proof of Concept

1. Assume stETH/ETH Chainlink feed goes stale at price `1.05e18` (true market price drops to `0.95e18` due to a depeg event).
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0)`.
3. `getRsETHAmountToMint` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `1.05e18`.
4. rsETH minted = `(100e18 * 1.05e18) / rsETHPrice` — attacker receives ~10.5% more rsETH than the true value of their deposit.
5. Attacker immediately requests withdrawal, redeeming rsETH at the correct (lower) TVL-backed price, extracting value from existing holders.

**Root cause line**: `ChainlinkPriceOracle.sol` line 52 — `(, int256 price,,,) = priceFeed.latestRoundData();` with no subsequent validation. [1](#0-0) 

**Consumption in deposit path**: `LRTDepositPool.sol` line 520 — `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();` [2](#0-1) 

**Consumption in TVL path**: `LRTOracle.sol` line 339 — `uint256 assetER = getAssetPrice(asset);` [3](#0-2) 

**Correct pattern already in repo** (not applied to `ChainlinkPriceOracle`): `ChainlinkOracleForRSETHPoolCollateral.sol` lines 30–32. [4](#0-3)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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
