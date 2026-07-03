### Title
Stale Chainlink Price Triggers Incorrect Auto-Pause, Freezing All User Deposits and Withdrawals — (`contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` does not validate Chainlink data freshness. When a Chainlink feed goes stale, the stale price propagates into `LRTOracle._updateRsETHPrice()`, which may compute a `newRsETHPrice` that deviates from `highestRsethPrice` beyond `pricePercentageLimit`. This triggers an automatic protocol-wide pause that freezes all user deposits and withdrawals — the same oracle-staleness-blocks-critical-operations pattern as the FlatMoney report.

---

### Finding Description

**Root cause — no staleness validation in `ChainlinkPriceOracle`:**

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards `updatedAt`, `answeredInRound`, and the sign of `price`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();          // ← no staleness check
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
``` [1](#0-0) 

There is no check on `answeredInRound >= roundId`, no `updatedAt > 0` guard, no maximum-age heartbeat check, and no `price > 0` assertion.

**Propagation path — stale price reaches the auto-pause trigger:**

`LRTOracle.updateRSETHPrice()` is a `public` function callable by anyone:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [2](#0-1) 

Inside `_updateRsETHPrice()`, `_getTotalEthInProtocol()` iterates over every supported asset and calls `getAssetPrice(asset)` for each one: [3](#0-2) 

If any Chainlink feed is stale and returns an outdated (lower) price, `totalETHInProtocol` is understated, and `newRsETHPrice` falls below `highestRsethPrice`.

**Auto-pause trigger — the safety mechanism fires on oracle staleness:**

```solidity
if (newRsETHPrice < highestRsethPrice) {
    uint256 diff = highestRsethPrice - newRsETHPrice;
    bool isPriceDecreaseOffLimit =
        pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

    if (isPriceDecreaseOffLimit) {
        if (!lrtDepositPool.paused()) lrtDepositPool.pause();
        if (!withdrawalManager.paused()) withdrawalManager.pause();
        _pause();
        return;
    }
}
``` [4](#0-3) 

When triggered, `lrtDepositPool`, `withdrawalManager`, and `LRTOracle` itself are all paused atomically. No user can deposit or withdraw until an admin manually unpauses each contract. The `pricePercentageLimit` comment states 1 % = `1e16`, so even a modest stale-price deviation is sufficient to trigger this path.

---

### Impact Explanation

**Temporary freezing of funds (Medium).** All user deposits and all withdrawal completions are blocked for the duration of the outage. The `LRTWithdrawalManager` is paused, so `completeWithdrawal()` and `instantWithdrawal()` both revert. The `LRTDepositPool` is paused, blocking new deposits. Recovery requires admin intervention to unpause three separate contracts after the Chainlink feed recovers. [5](#0-4) 

---

### Likelihood Explanation

Chainlink feeds can go stale during L1 network congestion, missed heartbeats, or oracle operator issues. Because `ChainlinkPriceOracle` performs zero freshness validation, any stale round is silently accepted. The trigger function `updateRSETHPrice()` is `public` — any user (or bot) can call it at any time, including during a period when a feed is stale. The combination of a realistic external event (feed staleness) and a permissionless trigger makes this a credible scenario.

---

### Recommendation

Add standard Chainlink staleness guards to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(answeredInRound >= roundId, "Stale price");
require(updatedAt > 0, "Incomplete round");
require(price > 0, "Invalid price");
require(block.timestamp - updatedAt <= MAX_STALENESS, "Price too old");
```

`MAX_STALENESS` should be set per feed based on its published heartbeat (e.g., 3 600 s for a 1-hour heartbeat feed). Additionally, consider adding a circuit-breaker that falls back to the last known good price rather than triggering a full protocol pause when a single feed goes stale.

---

### Proof of Concept

1. The stETH/ETH Chainlink feed misses its heartbeat and its last stored round is 24 hours old.
2. An unprivileged user (or a keeper bot) calls `LRTOracle.updateRSETHPrice()`.
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)`, which returns the 24-hour-old price without reverting.
4. `totalETHInProtocol` is understated; `newRsETHPrice` is computed below `highestRsethPrice`.
5. If the deviation exceeds `pricePercentageLimit` (e.g., 1 %), the downside-protection branch executes: `lrtDepositPool.pause()`, `withdrawalManager.pause()`, `_pause()`.
6. All subsequent `deposit()`, `completeWithdrawal()`, and `instantWithdrawal()` calls revert with `ContractPaused` / `Pausable: paused` until an admin manually unpauses all three contracts. [1](#0-0) [4](#0-3) [2](#0-1)

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

**File:** contracts/LRTWithdrawalManager.sol (L183-185)
```text
    function completeWithdrawal(address asset, string calldata referralId) external nonReentrant whenNotPaused {
        _processWithdrawalCompletion(asset, msg.sender, referralId);
    }
```
