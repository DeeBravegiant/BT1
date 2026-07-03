### Title
Missing Chainlink Staleness Validation Enables Stale-Price-Triggered Auto-Pause, Permanently Freezing Unclaimed Yield — (`contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice` discards `updatedAt` and `answeredInRound` from `latestRoundData`. When a Chainlink feed goes stale, the last-known (lower) price is silently accepted, understating `totalETHInProtocol`. If the resulting `newRsETHPrice` falls more than `pricePercentageLimit` below `highestRsethPrice`, `_updateRsETHPrice` auto-pauses the deposit pool, withdrawal manager, and oracle itself, permanently blocking fee minting and withdrawal unlocking until an admin manually intervenes.

---

### Finding Description

**Root cause — no staleness check in `ChainlinkPriceOracle.getAssetPrice`:** [1](#0-0) 

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();   // updatedAt, answeredInRound silently dropped
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

The return tuple is `(roundId, answer, startedAt, updatedAt, answeredInRound)`. All fields except `answer` are discarded. There is no `require(updatedAt >= block.timestamp - maxStaleness)` or `require(answeredInRound >= roundId)` guard anywhere in the call chain.

**How the stale price propagates to auto-pause:**

`_getTotalEthInProtocol` calls `getAssetPrice` for every supported asset: [2](#0-1) 

LST assets (stETH, cbETH, etc.) accrue value continuously. If the Chainlink feed is stale by even a few days, the returned price is lower than the true current price, so `totalETHInProtocol` is understated.

`_updateRsETHPrice` then computes `newRsETHPrice` from that understated total and compares it against `highestRsethPrice`: [3](#0-2) 

```solidity
if (newRsETHPrice < highestRsethPrice) {
    uint256 diff = highestRsethPrice - newRsETHPrice;
    bool isPriceDecreaseOffLimit =
        pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;          // ← early return; rsETHPrice never updated; fee never minted
    }
}
```

Once `_pause()` is called, `updateRSETHPrice()` is gated by `whenNotPaused`: [4](#0-3) 

Fee minting and withdrawal unlocking are frozen until `onlyLRTAdmin` calls `unpause()`.

---

### Impact Explanation

- **Fee minting frozen**: `_updateRsETHPrice` returns early at the pause branch; `IRSETH.mint` for protocol fees is never reached.
- **Withdrawals frozen**: `withdrawalManager` is paused, blocking all withdrawal claims.
- **Self-reinforcing**: `updateRSETHPrice()` (public) is blocked by `whenNotPaused`; only `updateRSETHPriceAsManager()` bypasses the pause, but that still cannot un-pause the deposit pool or withdrawal manager.
- Recovery requires explicit admin `unpause()` calls on three contracts.

Scoped impact: **Medium — Permanent freezing of unclaimed yield**.

---

### Likelihood Explanation

Chainlink feeds can go stale due to network congestion, node outages, or deviation-threshold not being crossed during a low-volatility period. LST/LRT assets appreciate ~4–5% APY; a feed stale for ~7–14 days can produce a price gap exceeding a typical `pricePercentageLimit` of 1–5%. The trigger function `updateRSETHPrice()` is public and callable by anyone, so no privileged role is needed to fire the auto-pause once the feed is stale. No oracle operator compromise is required — normal Chainlink liveness failure is sufficient.

---

### Recommendation

Add staleness and round-completeness checks in `ChainlinkPriceOracle.getAssetPrice`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(answeredInRound >= roundId, "Stale round");
require(updatedAt >= block.timestamp - maxStaleness, "Stale price");
require(price > 0, "Non-positive price");
```

`maxStaleness` should be set per-feed (e.g., 3600 s for ETH/USD, 86 400 s for slower feeds) and stored in `ChainlinkPriceOracle` alongside `assetPriceFeed`.

---

### Proof of Concept

```solidity
// Fork test outline (no public-mainnet state mutation)
function testStaleOracleTriggersAutoPause() public {
    // 1. Fork mainnet at block B where stETH/ETH feed was last updated T0
    // 2. vm.warp(T0 + 8 days);  // feed is now 8 days stale
    // 3. Call lrtOracle.updateRSETHPrice() as an unprivileged EOA
    // 4. Assert lrtDepositPool.paused() == true
    // 5. Assert lrtOracle.paused() == true
    // 6. Assert lrtOracle.updateRSETHPrice() reverts with ContractPaused
    // 7. Assert no fee was minted (rsETH treasury balance unchanged)
}
```

The threshold at which auto-pause fires is:

```
staleness_days ≥ (pricePercentageLimit / annualLSTYield) * 365
```

For `pricePercentageLimit = 1e16` (1%) and 5% APY: ≈ 73 days. For 5% limit: ≈ 365 days. For feeds with higher deviation (e.g., a depeg event captured in the last round), the threshold is reached immediately.

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
