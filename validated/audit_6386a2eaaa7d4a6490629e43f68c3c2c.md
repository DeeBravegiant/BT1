### Title
Missing Chainlink `latestRoundData()` Return Value Validation Enables Stale/Zero Price Acceptance - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards all safety-relevant return values (`updatedAt`, `answeredInRound`, `roundId`). No staleness check, no zero-price guard, and no negative-price guard are applied. This stale or zero price propagates directly into rsETH minting math and TVL accounting, enabling incorrect rsETH issuance or an unintended protocol-wide auto-pause that temporarily freezes all deposits and withdrawals.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All four safety-relevant return values — `roundId`, `startedAt`, `updatedAt`, `answeredInRound` — are discarded. The contract performs no check equivalent to:

```solidity
if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
```

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repository performs all three of these checks correctly: [2](#0-1) 

The unvalidated price from `ChainlinkPriceOracle.getAssetPrice()` is consumed by `LRTOracle.getAssetPrice()`: [3](#0-2) 

Which is called in two critical paths:

**Path 1 — rsETH minting ratio** (`LRTDepositPool.getRsETHAmountToMint()`): [4](#0-3) 

**Path 2 — TVL accounting** (`LRTOracle._getTotalEthInProtocol()`): [5](#0-4) 

---

### Impact Explanation

**Scenario A — Stale price returns 0 (feed paused or sequencer down):**

If `latestRoundData()` returns `price = 0` for a supported LST asset, `getAssetPrice()` returns `0`. Inside `_getTotalEthInProtocol()`, that asset's entire TVL contribution is zeroed out. The computed `newRsETHPrice` drops artificially. If the drop exceeds `pricePercentageLimit`, the auto-pause logic at lines 277–281 fires: [6](#0-5) 

This pauses `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` simultaneously — a **temporary freeze of all user funds** (deposits and withdrawals blocked) triggered by a stale oracle reading, not a real price drop.

**Scenario B — Stale non-zero price used for minting:**

If the feed is stale but returns a last-known non-zero price that no longer reflects market reality (e.g., an LST depegs but the feed hasn't updated), `getRsETHAmountToMint()` mints rsETH at the wrong ratio. A depositor can receive more rsETH than their deposit is worth in ETH terms, diluting existing holders — **theft of unclaimed yield / share mis-accounting**.

---

### Likelihood Explanation

Chainlink feeds can go stale during network congestion, sequencer outages (on L2 deployments), or when a feed is deprecated and stops updating. The `answeredInRound < roundId` condition is a documented Chainlink staleness indicator. The protocol already handles this correctly in `ChainlinkOracleForRSETHPoolCollateral`, demonstrating awareness of the risk — but the primary deposit-path oracle (`ChainlinkPriceOracle`) was left unprotected. Any supported LST asset whose Chainlink feed experiences a gap triggers this path without any privileged action required.

---

### Recommendation

Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral.getRate()` to `ChainlinkPriceOracle.getAssetPrice()`:

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

Additionally, consider adding a heartbeat-based staleness check (`block.timestamp - updatedAt > MAX_DELAY`) per feed.

---

### Proof of Concept

1. A supported LST asset's Chainlink feed goes stale (e.g., sequencer outage on an L2 deployment, or feed deprecation). `latestRoundData()` returns `price = 0` and `answeredInRound < roundId`.
2. Anyone calls `LRTOracle.updateRSETHPrice()` (public, no access control): [7](#0-6) 
3. `_updateRsETHPrice()` calls `_getTotalEthInProtocol()`, which calls `getAssetPrice(staleLSTAsset)` → returns `0`.
4. The stale asset's TVL contribution is zeroed. `newRsETHPrice` drops below `highestRsethPrice` by more than `pricePercentageLimit`.
5. The auto-pause fires: `LRTDepositPool.pause()`, `LRTWithdrawalManager.pause()`, `LRTOracle._pause()` are all called. [8](#0-7) 
6. All user deposits and withdrawals are frozen until an admin manually unpauses — triggered by a stale oracle reading with no attacker capital required.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-33)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

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

**File:** contracts/LRTOracle.sol (L270-281)
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
