### Title
Missing Staleness Check in `ChainlinkPriceOracle` Allows Any Caller to Trigger Unintended Protocol-Wide Pause, Freezing Deposits and Withdrawals - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` consumes Chainlink `latestRoundData()` without validating staleness (`updatedAt`, `answeredInRound`). A stale price that appears lower than the true market price, combined with the public `LRTOracle.updateRSETHPrice()` entry point, allows any unprivileged caller to trigger the automatic downside-protection pause in `LRTOracle._updateRsETHPrice()`, freezing all deposits and withdrawals until an admin manually unpauses.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and discards every return value except `price`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52
(, int256 price,,,) = priceFeed.latestRoundData();
```

No check is made on `updatedAt` (heartbeat staleness), `answeredInRound < roundId` (incomplete round), or `price <= 0`. This contrasts with `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which explicitly guards all three conditions.

The stale price propagates through the following call chain:

1. `LRTOracle.getAssetPrice(asset)` → `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)` (i.e., `ChainlinkPriceOracle`)
2. `LRTOracle._getTotalEthInProtocol()` sums `totalAssetAmt * assetER` for every supported LST
3. `LRTOracle._updateRsETHPrice()` computes `newRsETHPrice = totalETHInProtocol / rsethSupply`
4. If `newRsETHPrice < highestRsethPrice` and the difference exceeds `pricePercentageLimit * highestRsethPrice`, the function calls:
   - `lrtDepositPool.pause()`
   - `withdrawalManager.pause()`
   - `_pause()` (oracle itself)
   - and **returns early**, never updating `rsETHPrice`

`updateRSETHPrice()` is declared `public` with no role restriction — any EOA or contract can call it.

---

### Impact Explanation

When a Chainlink feed for any supported LST (stETH, ETHx, rETH, sfrxETH) goes stale — e.g., during network congestion, a Chainlink node outage, or a sequencer downtime on L2 — the last reported price may be materially lower than the true market price. An unprivileged caller who invokes `updateRSETHPrice()` at that moment causes the computed `newRsETHPrice` to fall below `highestRsethPrice` by more than `pricePercentageLimit`. This triggers the automatic downside-protection pause, freezing:

- All user deposits (`LRTDepositPool` paused)
- All user withdrawals (`LRTWithdrawalManager` paused)
- All oracle price updates (`LRTOracle` paused)

The pause persists until an admin calls `unpause()` on each contract. This constitutes **temporary freezing of funds** for all protocol users.

---

### Likelihood Explanation

Chainlink LST/ETH feeds (e.g., stETH/ETH) have 24-hour heartbeats and 0.5% deviation thresholds. During periods of high gas prices or network stress, updates can lag. If `pricePercentageLimit` is set to a tight value (e.g., 1–2%), even a modest staleness-induced price discrepancy is sufficient to trigger the pause. The trigger is permissionless — any user, bot, or MEV searcher can call `updateRSETHPrice()` at the worst moment.

---

### Recommendation

Add staleness and validity checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
if (block.timestamp - updatedAt > HEARTBEAT_THRESHOLD) revert StalePrice();
```

Additionally, consider restricting `updateRSETHPrice()` to a keeper role or adding a circuit-breaker that distinguishes genuine price drops from oracle failures before triggering a protocol-wide pause.

---

### Proof of Concept

**Root cause — no staleness check:** [1](#0-0) 

**Contrast — staleness checks present in the pool oracle wrapper:** [2](#0-1) 

**Stale price flows into TVL computation:** [3](#0-2) 

**Downside-protection auto-pause triggered by apparent price drop:** [4](#0-3) 

**Public, permissionless entry point any caller can invoke:** [5](#0-4) 

**Attack scenario:**

1. A Chainlink LST/ETH feed (e.g., stETH/ETH) goes stale for several hours; the last reported price is 1% below the true market price.
2. `pricePercentageLimit` is set to 1% (1e16).
3. Any unprivileged user calls `LRTOracle.updateRSETHPrice()`.
4. `_getTotalEthInProtocol()` uses the stale low price → `newRsETHPrice` is 1% below `highestRsethPrice`.
5. `isPriceDecreaseOffLimit` evaluates to `true` → `lrtDepositPool.pause()`, `withdrawalManager.pause()`, `_pause()` are called.
6. All user deposits and withdrawals are frozen until admin manually unpauses each contract.

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
