### Title
No Time-Based Staleness Check on Chainlink Price Feeds Allows Stale Prices to Drive Incorrect rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all staleness-related return values (`updatedAt`, `answeredInRound`). There is no check that the price was updated within an acceptable heartbeat window. A stale Chainlink price (e.g., during a network outage or LST depeg event where the feed lags) is silently accepted and propagated into rsETH minting calculations, allowing depositors to receive more rsETH than they are entitled to.

### Finding Description
In `ChainlinkPriceOracle.getAssetPrice()`, the call to `latestRoundData()` only captures the `price` field:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
```

The `updatedAt` timestamp and `answeredInRound` values are both silently discarded. No check of the form `block.timestamp - updatedAt > heartbeat` is performed, and no `answeredInRound < roundId` guard is applied. [1](#0-0) 

This price is consumed by `LRTOracle.getAssetPrice()`, which delegates directly to the registered `IPriceFetcher`: [2](#0-1) 

`LRTDepositPool.getRsETHAmountToMint()` then uses this live oracle price to compute how many rsETH tokens to mint per unit of deposited LST:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

This value is used directly in `_beforeDeposit()` → `depositAsset()` to mint rsETH to the depositor: [4](#0-3) 

Additionally, `_getTotalEthInProtocol()` uses the same stale price to compute the total ETH backing rsETH, which feeds into `_updateRsETHPrice()` and the protocol fee calculation: [5](#0-4) 

By contrast, `ChainlinkOracleForRSETHPoolCollateral` — a sibling oracle in the same repo — does apply a round-based staleness check (`answeredInRound < roundID`), confirming the protocol is aware of the pattern but failed to apply it in `ChainlinkPriceOracle`: [6](#0-5) 

### Impact Explanation
If a Chainlink LST/ETH feed (e.g., stETH/ETH, rETH/ETH) goes stale at a price that is inflated relative to the actual current market price — a realistic scenario during an LST depeg event where the oracle lags — then `getAssetPrice()` returns the stale high price. A depositor calling `depositAsset()` at that moment receives more rsETH than the deposited asset is actually worth, diluting all existing rsETH holders. This constitutes theft of unclaimed yield (High impact).

The same stale price fed into `_updateRsETHPrice()` can also cause `newRsETHPrice` to be computed incorrectly, potentially triggering the downside protection pause if the stale price is lower than actual, resulting in a temporary freeze of funds (Medium impact).

### Likelihood Explanation
Chainlink LST/ETH feeds carry 24-hour heartbeats and 0.5% deviation thresholds. During periods of network congestion, oracle node failures, or rapid LST price movement (depeg), the feed can lag by more than the heartbeat. This is a known, documented risk class for Chainlink integrations. The entry path (`depositAsset`) is fully permissionless and requires no special role.

### Recommendation
Add a time-based staleness check in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (block.timestamp - updatedAt > STALENESS_THRESHOLD) revert StalePrice();
```

The `STALENESS_THRESHOLD` should be set per feed based on its documented heartbeat (e.g., 24 hours + a small buffer, consistent with the recommendation in the referenced API3 report).

### Proof of Concept

1. Chainlink stETH/ETH feed last updated at `T - 25h` (stale, heartbeat exceeded). Feed price is `1.01e18` (stale high; actual market is `0.99e18` due to a depeg).
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
3. `getRsETHAmountToMint(stETH, 100e18)` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns `1.01e18` (stale, no revert).
4. `rsethAmountToMint = (100e18 * 1.01e18) / rsETHPrice` — attacker receives ~2% more rsETH than the deposited stETH is actually worth.
5. Existing rsETH holders are diluted; the attacker has extracted yield at their expense.
6. No admin action, no special role, no oracle operator compromise required — the entry path is fully public. [1](#0-0) [7](#0-6)

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
