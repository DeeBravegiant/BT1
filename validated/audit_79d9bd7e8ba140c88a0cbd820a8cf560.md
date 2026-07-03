Audit Report

## Title
Stale Chainlink Price Accepted Without Timestamp Validation in `ChainlinkPriceOracle` — (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `answer`, performing no staleness check (`updatedAt`), no round-completeness check (`answeredInRound >= roundId`), and no sign check (`price > 0`). This stale price flows directly into rsETH minting and TVL accounting, enabling any depositor to receive over-minted rsETH at the expense of existing holders when a Chainlink feed is stale.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` at line 52 binds only `price` from the five return values of `latestRoundData()`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

`roundId`, `startedAt`, `updatedAt`, and `answeredInRound` are all silently discarded. No check is made that `price > 0`, `updatedAt != 0`, or `answeredInRound >= roundId`.

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` — used for the L2 pool — correctly validates all three conditions:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L27-32
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The unvalidated `ChainlinkPriceOracle` is the oracle registered for supported LST assets. `LRTOracle.getAssetPrice()` delegates directly to it: [3](#0-2) 

This stale price is consumed in two critical paths:

**Path 1 — rsETH minting:** `LRTDepositPool.getRsETHAmountToMint()` uses `lrtOracle.getAssetPrice(asset)` as the numerator for computing how many rsETH tokens to mint per deposited LST unit: [4](#0-3) 

**Path 2 — rsETH price update:** `LRTOracle._getTotalEthInProtocol()` calls `getAssetPrice(asset)` for every supported asset to compute total protocol TVL, which then sets `rsETHPrice`: [5](#0-4) 

The `pricePercentageLimit` guard in `_updateRsETHPrice()` does not protect the minting path, because `depositAsset()` uses the already-stored `lrtOracle.rsETHPrice()` and the live (stale) Chainlink price independently — the guard only fires during a separate `updateRSETHPrice()` call. [6](#0-5) 

## Impact Explanation
**High — Theft of unclaimed yield.**

If a Chainlink LST/ETH feed goes stale at a price higher than the true current price (e.g., after a slashing event not yet reflected on-chain, or during Chainlink node downtime), `getRsETHAmountToMint` mints more rsETH than the deposited LST is actually worth. The over-minted rsETH dilutes all existing rsETH holders, transferring value from existing holders to the depositor. This is a concrete, quantifiable loss of yield/value for existing holders, directly matching the "High — Theft of unclaimed yield" impact class.

## Likelihood Explanation
**Medium.** Chainlink LST/ETH feeds have documented heartbeat intervals (1–24 hours). During L1 congestion, Chainlink node outages, or sequencer downtime, feeds can remain stale beyond their heartbeat. LST prices are not static — slashing events, depeg events, or reward accrual can cause meaningful price movement within a staleness window. The attack requires no privileged access: any user can call `depositAsset()` or `depositETH()` while the feed is stale, with `minRSETHAmountExpected` set to 0.

## Recommendation
Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();
require(answeredInRound >= roundId, "Stale price");
require(updatedAt != 0, "Incomplete round");
require(price > 0, "Invalid price");
require(block.timestamp - updatedAt <= MAX_STALENESS, "Price too old");
```

Store a per-feed `MAX_STALENESS` parameter matching the Chainlink heartbeat for each feed, and revert if the price is outside the acceptable window.

## Proof of Concept
1. Chainlink's LST/ETH feed for `stETH` goes stale (heartbeat missed during congestion). Last reported price: `1.05e18`; true price has dropped to `0.98e18` due to a slashing event.
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
3. `_beforeDeposit` → `getRsETHAmountToMint` computes: `(100e18 * 1.05e18) / rsETHPrice` — using the stale inflated price.
4. Attacker receives rsETH worth more than the 100 stETH deposited at true market value.
5. Existing rsETH holders are diluted by the difference.
6. No admin action, no privileged role, and no special setup required — attacker only needs to time the deposit while the feed is stale.

**Foundry fork test plan:** Fork mainnet at a block where a Chainlink LST/ETH feed's `updatedAt` is beyond its heartbeat. Deploy or point to the existing `ChainlinkPriceOracle`. Call `depositAsset` with a large LST amount. Assert that `rsethAmountToMint` exceeds the fair value computed using the true current price from an alternative source (e.g., on-chain DEX TWAP). Confirm no revert occurs despite the stale feed.

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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
