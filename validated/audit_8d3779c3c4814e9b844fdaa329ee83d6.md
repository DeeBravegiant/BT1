### Title
No Staleness Check on Chainlink Price Feed Allows Stale Prices to Corrupt rsETH Valuation and Trigger Protocol Pause - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards the `updatedAt` timestamp and `answeredInRound` fields, accepting arbitrarily stale prices with no freshness validation. This stale price propagates into `LRTOracle._updateRsETHPrice()`, which can produce an incorrect `newRsETHPrice` that triggers the protocol's automatic downside-protection pause, temporarily freezing all deposits and withdrawals.

---

### Finding Description

In `contracts/oracles/ChainlinkPriceOracle.sol`, `getAssetPrice()` is:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();          // updatedAt ignored
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
``` [1](#0-0) 

The five return values of `latestRoundData()` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. Only `answer` (`price`) is used. `updatedAt` and `answeredInRound` are completely discarded. There is no `require(block.timestamp - updatedAt <= heartbeat)` guard and no `require(answeredInRound >= roundId)` guard.

This oracle is the sole price source consumed by `LRTOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
    return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
}
``` [2](#0-1) 

`getAssetPrice()` is called inside `_getTotalEthInProtocol()`, which feeds directly into `_updateRsETHPrice()`:

```solidity
uint256 assetER = getAssetPrice(asset);
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [3](#0-2) 

`_updateRsETHPrice()` then computes `newRsETHPrice` and applies downside protection:

```solidity
if (newRsETHPrice < highestRsethPrice) {
    ...
    bool isPriceDecreaseOffLimit =
        pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
``` [4](#0-3) 

LST asset prices in ETH terms are monotonically non-decreasing. If a Chainlink feed goes stale, the stale (older, lower) price is used. The resulting `newRsETHPrice` will be lower than `highestRsethPrice`. If the gap exceeds `pricePercentageLimit`, the protocol auto-pauses `LRTDepositPool` and `LRTWithdrawalManager`, freezing all user deposits and withdrawals until an admin manually unpauses.

By contrast, `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` — a different oracle wrapper in the same repo — does perform staleness checks (`answeredInRound < roundID`, `timestamp == 0`), confirming the team is aware of the pattern but did not apply it to `ChainlinkPriceOracle`. [5](#0-4) 

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

A stale Chainlink price for any supported LST asset causes `_updateRsETHPrice()` to compute a deflated `newRsETHPrice`. If the deflation exceeds `pricePercentageLimit`, the protocol's own downside-protection logic pauses both `LRTDepositPool` and `LRTWithdrawalManager`. Users cannot deposit collateral or initiate withdrawals until an admin calls `unpause()`. Funds are not lost but are inaccessible for the duration of the pause.

---

### Likelihood Explanation

Chainlink feeds can go stale during Ethereum network congestion, when the price deviation threshold is not breached for an extended period, or during oracle infrastructure incidents. `updateRSETHPrice()` is a public, permissionless function callable by anyone. Any caller — including an automated keeper or a regular user — will trigger the stale-price path whenever the feed has not been updated within the expected heartbeat. No attacker action is required beyond calling the public function at the right moment.

---

### Recommendation

Add staleness and validity checks in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(answeredInRound >= roundId, "Stale price: round not complete");
require(updatedAt != 0, "Stale price: incomplete round");
require(block.timestamp - updatedAt <= MAX_STALENESS_SECONDS, "Stale price: too old");
require(price > 0, "Invalid price");
```

`MAX_STALENESS_SECONDS` should be set per feed based on its documented heartbeat (e.g., 86 400 s for 24 h feeds, 3 600 s for 1 h feeds).

---

### Proof of Concept

1. A supported LST asset (e.g., stETH) has its Chainlink feed go stale — the feed's last update was 25 hours ago (heartbeat = 24 h), but the price has not moved enough to trigger a deviation update.
2. Any unprivileged user calls `LRTOracle.updateRSETHPrice()`.
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)`, which returns the 25-hour-old price — lower than the true current price by, say, 1.5%.
4. `newRsETHPrice` is computed using this deflated asset valuation and falls below `highestRsethPrice` by more than `pricePercentageLimit` (e.g., 1% = `1e16`).
5. `_updateRsETHPrice()` executes the downside-protection branch: `lrtDepositPool.pause()`, `withdrawalManager.pause()`, `_pause()`.
6. All user deposits and withdrawals are frozen. The protocol remains paused until an admin with `DEFAULT_ADMIN_ROLE` calls `unpause()` on each contract. [1](#0-0) [4](#0-3)

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
