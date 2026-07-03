### Title
No Staleness Validation on Chainlink Price Feed Allows Stale Price to Inflate rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all freshness-related return values (`updatedAt`, `answeredInRound`). The returned spot price is consumed directly by `LRTDepositPool.getRsETHAmountToMint()` to determine how many rsETH tokens a depositor receives, and by `LRTOracle._updateRsETHPrice()` to set the global rsETH/ETH rate. A stale inflated price allows a depositor to receive more rsETH than their deposit is worth, diluting all existing rsETH holders.

### Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol` line 52, `latestRoundData()` is called with a five-value destructure that silently drops `updatedAt` and `answeredInRound`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
```

No check of the form `if (block.timestamp - updatedAt > MAX_DELAY) revert StalePrice()` or `if (answeredInRound < roundId) revert StalePrice()` is present. This is in direct contrast to `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`, which is used in the pool path and does validate both conditions:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
```

The unvalidated price from `ChainlinkPriceOracle` flows into two critical computations:

1. **`LRTDepositPool.getRsETHAmountToMint()`** (line 520): `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()` — uses the live oracle price at deposit time.
2. **`LRTOracle._getTotalEthInProtocol()`** (line 339): `uint256 assetER = getAssetPrice(asset)` — used to compute total protocol TVL and set the stored `rsETHPrice`.

### Impact Explanation
When a Chainlink feed is stale and its last reported price is higher than the true current price (e.g., the LST has depegged but the feed has not updated), a depositor calling `depositAsset()` or `depositETH()` receives `rsethAmountToMint = amount * stalePriceHigh / rsETHPrice`. The numerator is inflated, so the depositor receives more rsETH than the ETH value of their deposit warrants. This dilutes all existing rsETH holders proportionally — their rsETH redeems for less ETH than it should. This constitutes theft of unclaimed yield from existing holders.

A secondary path: if the stale price is lower than actual, anyone can call the public `updateRSETHPrice()` to push `newRsETHPrice` below `highestRsethPrice` by more than `pricePercentageLimit`, triggering an automatic protocol-wide pause (deposit pool, withdrawal manager, and oracle all paused), temporarily freezing all user funds.

**Impact: High — Theft of unclaimed yield (primary); Medium — Temporary freezing of funds (secondary)**

### Likelihood Explanation
Chainlink feeds have heartbeat intervals (e.g., 1 hour for stETH/ETH on mainnet, 24 hours for some feeds). During network congestion, oracle node outages, or L2 sequencer downtime, feeds can go stale for extended periods. No special privilege is required: `depositAsset()` is callable by any user, and `updateRSETHPrice()` is a public function with no access control. An attacker only needs to observe a stale feed and act within the staleness window.

### Recommendation
Add staleness and completeness checks to `ChainlinkPriceOracle.getAssetPrice()`, consistent with the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (block.timestamp - updatedAt > MAX_STALENESS_DELAY) revert StalePrice();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_STALENESS_DELAY` should be set per-feed based on its documented heartbeat interval.

### Proof of Concept

**Setup:** stETH/ETH Chainlink feed has not updated for 2 hours (within its 24-hour heartbeat but the price has moved 2% downward due to a depeg event). The last reported price is `1.02e18` but the true price is `1.00e18`.

**Attack (yield theft path):**

1. Attacker observes the stale feed still reports `1.02e18` for stETH/ETH.
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, minRSETH, "")`.
3. `getRsETHAmountToMint(stETH, 100e18)` executes:
   - `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `1.02e18`
   - `rsethAmountToMint = 100e18 * 1.02e18 / rsETHPrice`
   - Attacker receives rsETH worth `102 ETH` in exchange for `100 stETH` (true value `100 ETH`).
4. The 2 ETH excess rsETH is backed by no real assets — existing holders' rsETH is diluted by 2 ETH of phantom value.

**Attack (pause path):**

1. Stale feed reports `0.98e18` for stETH/ETH (true price `1.00e18`), a 2% drop.
2. `pricePercentageLimit` is set to `1e16` (1%).
3. Anyone calls `updateRSETHPrice()`.
4. `_getTotalEthInProtocol()` uses the stale low price → `newRsETHPrice` drops by >1% from `highestRsethPrice`.
5. Lines 277–281 execute: deposit pool, withdrawal manager, and oracle are all paused.
6. All user deposits and withdrawals are frozen until admin manually unpauses.

**Root cause lines:** [1](#0-0) 

**Contrast with the pool oracle that does validate staleness:** [2](#0-1) 

**Downstream consumption at deposit time:** [3](#0-2) 

**Downstream consumption in rsETH price update:** [4](#0-3) 

**Pause trigger on stale low price:** [5](#0-4)

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

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
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

**File:** contracts/LRTOracle.sol (L331-344)
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

```
