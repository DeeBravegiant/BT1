### Title
Missing Staleness and Zero-Value Validation in Chainlink Price Feed — (`contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls Chainlink's `latestRoundData()` but discards all return values except `price`. It performs no heartbeat/staleness check on `updatedAt` and no zero-value guard on the returned `int256 price`. A stale or zero Chainlink answer propagates unchecked into `LRTOracle._updateRsETHPrice()`, which is publicly callable, and can produce an incorrect rsETH price that triggers the protocol's automatic downside-protection pause, temporarily freezing all user deposits and withdrawals.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` reads the Chainlink feed as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All five return values of `latestRoundData()` — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — are available, but only `price` is used. The contract:

1. **Does not check `updatedAt`** against any heartbeat threshold, so a stale price (e.g., during Chainlink downtime, sequencer outage on L2, or feed deprecation) is silently accepted.
2. **Does not validate `price > 0`**, so a zero answer (Chainlink circuit-breaker scenario) produces `assetER = 0`, zeroing out that asset's contribution to TVL.
3. **Does not check `answeredInRound >= roundId`**, so an incomplete round is accepted.

By contrast, the sibling contract `ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repository correctly performs all three checks:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The unchecked price from `ChainlinkPriceOracle` flows into `LRTOracle.getAssetPrice()` → `LRTOracle._getTotalEthInProtocol()` → `LRTOracle._updateRsETHPrice()`. [3](#0-2) 

`updateRSETHPrice()` is a public function with no access control beyond `whenNotPaused`: [4](#0-3) 

---

### Impact Explanation

**Medium — Temporary freezing of funds.**

When a Chainlink feed goes stale (returning a price significantly below the true market price), any unprivileged caller can invoke `updateRSETHPrice()`. The stale price causes `_getTotalEthInProtocol()` to undercount TVL, producing a `newRsETHPrice` that is artificially depressed. If the computed drop exceeds `pricePercentageLimit`, the downside-protection logic at lines 277–281 automatically pauses `lrtDepositPool`, `withdrawalManager`, and `LRTOracle` itself:

```solidity
if (!lrtDepositPool.paused()) lrtDepositPool.pause();
if (!withdrawalManager.paused()) withdrawalManager.pause();
_pause();
``` [5](#0-4) 

This freezes all user deposits and withdrawals until an admin manually unpauses. A zero price (circuit-breaker scenario) produces the same outcome with certainty, as `assetER = 0` collapses the entire TVL contribution of the affected asset.

---

### Likelihood Explanation

Chainlink feeds have documented heartbeat intervals (e.g., 24 hours for some ETH-denominated pairs, 1 hour for others). During periods of low volatility, feeds may not update for the full heartbeat window. On L2 networks, sequencer downtime can cause feeds to go stale. These are realistic, non-adversarial conditions. Once a feed is stale, any external actor can call the public `updateRSETHPrice()` to trigger the incorrect price update and resulting pause.

---

### Recommendation

Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral.getRate()` to `ChainlinkPriceOracle.getAssetPrice()`:

1. Store a per-asset `heartbeat` (maximum acceptable staleness) alongside `assetPriceFeed`.
2. After calling `latestRoundData()`, check:
   - `answeredInRound >= roundId` (no incomplete round)
   - `updatedAt != 0` (round is complete)
   - `block.timestamp - updatedAt <= heartbeat` (price is fresh)
   - `price > 0` (valid answer)

This mirrors the fix recommended in the external report: use a per-asset dynamic staleness threshold rather than a single hard-coded value, and enforce non-zero price validation.

---

### Proof of Concept

1. Chainlink's stETH/ETH feed (or any feed registered in `ChainlinkPriceOracle`) goes stale — its `updatedAt` timestamp is older than the feed's heartbeat.
2. The stale price is, say, 10% below the true price (within normal market movement that Chainlink simply hasn't published yet).
3. An unprivileged attacker calls `LRTOracle.updateRSETHPrice()`.
4. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)`, which returns the stale (depressed) price with no revert.
5. `newRsETHPrice` is computed as artificially low.
6. If `(highestRsethPrice - newRsETHPrice) > pricePercentageLimit * highestRsethPrice`, the auto-pause fires, freezing `lrtDepositPool` and `withdrawalManager`.
7. All user deposits and withdrawals are blocked until an admin calls `unpause()`. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
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
