### Title
Missing Chainlink price validation (zero/negative price and staleness) in `ChainlinkPriceOracle` - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `price`, performing no validity checks. A zero, negative, or stale price is silently accepted and propagated into the rsETH exchange rate calculation, enabling incorrect rsETH pricing that harms depositors and withdrawers.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Three validations are absent:

1. **Zero/negative price not rejected.** If `price == 0`, the function returns `0`. If `price < 0`, the unchecked cast `uint256(price)` wraps to a very large number (two's complement). Neither case reverts.
2. **Staleness not checked.** The `updatedAt` timestamp is discarded. A feed that has not been updated for longer than its heartbeat interval (e.g., 24 h for ETH/USD) returns the last stored answer without any error.
3. **No L2 sequencer grace period.** On L2 deployments, the sequencer uptime feed and its `GRACE_PERIOD` are not consulted, so prices accumulated during sequencer downtime are consumed immediately on restart.

By contrast, the sibling contract `ChainlinkOracleForRSETHPoolCollateral` in the same repository performs all three checks (`answeredInRound < roundID`, `timestamp == 0`, `ethPrice <= 0`), confirming the pattern is known and intentionally applied elsewhere. [1](#0-0) [2](#0-1) 

The corrupted price flows directly into `LRTOracle._getTotalEthInProtocol()`, which sums `assetER * totalAssetAmt` for every supported asset, and then into `_updateRsETHPrice()`, which sets the global `rsETHPrice` used for all deposits and withdrawals. [3](#0-2) [4](#0-3) 

### Impact Explanation
**Scenario A – zero price returned for one asset:**
`totalETHInProtocol` is understated by the full ETH value of that asset. `newRsETHPrice` drops proportionally. If the drop exceeds `pricePercentageLimit`, the protocol auto-pauses (temporary freeze of all deposits and withdrawals for all users). If the drop is within the limit, `rsETHPrice` is written at the artificially low value; subsequent depositors receive more rsETH than they are entitled to, diluting all existing rsETH holders. [5](#0-4) [6](#0-5) 

**Scenario B – negative price returned:**
`uint256(negativeInt256)` wraps to a value near `2^256`, making `totalETHInProtocol` astronomically large. `newRsETHPrice` spikes, triggering `PriceAboveDailyThreshold` for unprivileged callers, or, if called by a manager, writing an absurd rsETH price that lets the caller redeem rsETH for far more ETH than deposited. [7](#0-6) 

**Scenario C – stale price:**
During network congestion or after an L2 sequencer restart, the last cached price (potentially hours old) is used. This misprices rsETH relative to current market conditions, enabling arbitrage at the expense of honest depositors or withdrawers.

Impact classification: **Medium – temporary freezing of funds** (Scenario A with pause) / **Low – contract fails to deliver promised returns** (Scenario C).

### Likelihood Explanation
Chainlink feeds occasionally go stale during extreme network congestion or L2 sequencer outages. A zero answer has been observed historically on Chainlink feeds during circuit-breaker events. The entry point `updateRSETHPrice()` is `public` and callable by any unprivileged address, so no special access is required to trigger the mispricing. [8](#0-7) 

Likelihood: **Low** (requires a rare Chainlink feed anomaly or sequencer event).

### Recommendation
Replace the bare `latestRoundData` call in `ChainlinkPriceOracle.getAssetPrice()` with full validation, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(uint80 roundId, int256 price, , uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
// staleness: compare updatedAt against a per-feed heartbeat + buffer
if (block.timestamp - updatedAt > HEARTBEAT + BUFFER) revert StalePrice();
```

For L2 deployments, additionally consult the Chainlink sequencer uptime feed and enforce the `GRACE_PERIOD` before consuming any price, as documented in the [Chainlink L2 sequencer feeds guide](https://docs.chain.link/data-feeds/l2-sequencer-feeds#example-code). [9](#0-8) 

### Proof of Concept
1. A Chainlink ETH/stETH feed goes stale (no update for >24 h during congestion) and returns a price 10% below the true market price.
2. Any address calls `LRTOracle.updateRSETHPrice()` (public, no access control).
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)`, which returns the stale low price without reverting.
4. `totalETHInProtocol` is understated; `newRsETHPrice` is computed ~10% below fair value.
5. If `pricePercentageLimit` is set to, e.g., 5%, the 10% drop triggers `_pause()`, `lrtDepositPool.pause()`, and `withdrawalManager.pause()` — all user deposits and withdrawals are frozen until an admin manually unpauses.
6. If `pricePercentageLimit` is unset or the drop is within the limit, `rsETHPrice` is written at the deflated value; a depositor immediately minting rsETH at this price receives ~11% more rsETH than fair value, diluting all existing holders. [10](#0-9) [11](#0-10) [8](#0-7)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L18-37)
```text
    error StalePrice();
    error IncompleteRound();
    error InvalidPrice();

    constructor(address _oracle) {
        oracle = _oracle;
    }

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

**File:** contracts/LRTOracle.sol (L230-232)
```text
        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

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
