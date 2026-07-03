### Title
No Staleness Threshold Enforced on Chainlink Price Feeds — Missing Security Parameter Analogous to `minBlockHeight` - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards every return value except `price`. There is no configurable maximum-age parameter (analogous to `minBlockHeight` in the reference report) and no time-based staleness check. A stale Chainlink feed silently propagates an incorrect asset price into `LRTOracle._updateRsETHPrice()`, causing `rsETHPrice` to be set to a wrong value. Depositors who interact with `LRTDepositPool` immediately after a stale-price update receive an incorrect number of rsETH tokens, either diluting existing holders or cheating the depositor.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` reads from a Chainlink aggregator but ignores the `updatedAt` timestamp and `answeredInRound` fields entirely:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-L54
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();   // updatedAt silently discarded
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
``` [1](#0-0) 

There is no configurable `maxStaleness` (or equivalent) parameter anywhere in the contract, and no lower-bound enforcement on how old a price can be before it is rejected — the exact structural gap the reference report identifies for `minBlockHeight`.

This price is consumed by `LRTOracle._getTotalEthInProtocol()`, which iterates over every supported asset and calls `getAssetPrice(asset)` for each one: [2](#0-1) 

The result feeds directly into `_updateRsETHPrice()`, which computes and stores the new `rsETHPrice`: [3](#0-2) 

`updateRSETHPrice()` is a **public, permissionless function** — any external caller can trigger a price update at any time: [4](#0-3) 

The stored `rsETHPrice` is then used by `LRTDepositPool` to determine how many rsETH tokens to mint per unit of deposited asset, directly affecting every depositor.

A secondary instance exists in `ChainlinkOracleForRSETHPoolCollateral`, used by L2 pool contracts to price collateral tokens. It checks `answeredInRound < roundID` and `timestamp == 0`, but performs **no time-based staleness check** (`block.timestamp - updatedAt > maxAge`): [5](#0-4) 

---

### Impact Explanation

**High — Theft of unclaimed yield / share mis-accounting.**

If a supported LST's Chainlink feed goes stale at a price lower than the true market price (e.g., feed pauses during high network load or a sequencer outage on an L2), `totalETHInProtocol` is underestimated. The resulting `rsETHPrice` is set below its true value. A depositor who calls `depositAsset()` immediately after `updateRSETHPrice()` is called with the stale price receives **more rsETH than their deposit is worth**, diluting all existing rsETH holders and effectively stealing a portion of their accrued yield. The inverse (stale price above true value) cheats the depositor.

---

### Likelihood Explanation

Chainlink feeds have documented heartbeat intervals (e.g., 1 hour for ETH/USD, 24 hours for some LST feeds). A feed can go stale due to: Chainlink node outages, network congestion preventing on-chain updates, or feed deprecation. Because `updateRSETHPrice()` is permissionless, an attacker monitoring mempool can call it the moment a stale price appears and before the feed recovers, locking in the incorrect `rsETHPrice` for the next deposit window. Likelihood is **medium** — it requires an external condition (feed staleness) but no privileged access.

---

### Recommendation

1. Add a configurable `maxStaleness` parameter (per asset or global) to `ChainlinkPriceOracle`, analogous to the `minBlockHeight` concept in the reference report. Enforce it in `getAssetPrice()`:

```solidity
uint256 public maxStaleness; // e.g. 3600 seconds

function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();
    require(answeredInRound >= roundId, "Stale round");
    require(block.timestamp - updatedAt <= maxStaleness, "Price too stale");
    require(price > 0, "Non-positive price");
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

2. Apply the same time-based staleness check to `ChainlinkOracleForRSETHPoolCollateral.getRate()`.
3. Enforce a minimum sane value for `maxStaleness` (e.g., refuse `maxStaleness == 0`) to prevent the equivalent of `minBlockHeight = 0`.

---

### Proof of Concept

1. Chainlink feed for `stETH/ETH` goes stale at price `0.98e18` (true price: `1.00e18`).
2. Attacker calls `LRTOracle.updateRSETHPrice()` (permissionless).
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns `0.98e18` with no staleness revert.
4. `totalETHInProtocol` is 2% lower than reality; `rsETHPrice` is set 2% below true value.
5. Attacker deposits `1 stETH` via `LRTDepositPool.depositAsset()`, receiving `~2%` more rsETH than fair value.
6. Feed recovers; next legitimate `updateRSETHPrice()` call restores correct price, but the attacker's excess rsETH remains, diluting all prior holders. [1](#0-0) [4](#0-3) [6](#0-5)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

```

**File:** contracts/LRTOracle.sol (L331-340)
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
