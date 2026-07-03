### Title
Missing Chainlink Price Validity Checks (Zero Price and Staleness) in `getAssetPrice` - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validation return values (`updatedAt`, `answeredInRound`, `roundId`), performing no zero-price check and no staleness check. The sister contract `ChainlinkOracleForRSETHPoolCollateral` performs all three checks. A stale or zero price accepted here propagates into `LRTOracle._updateRsETHPrice()`, which is publicly callable, corrupting the rsETH/ETH exchange rate and potentially triggering a false protocol-wide pause that temporarily freezes all user funds.

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values of `latestRoundData()` are available — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — but only `answer` is used. No check is made that:
- `price > 0` (zero-price guard)
- `updatedAt` is recent (staleness guard)
- `answeredInRound >= roundId` (incomplete-round guard) [1](#0-0) 

By contrast, the in-scope `ChainlinkOracleForRSETHPoolCollateral.getRate()` performs all three checks explicitly:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The stale/zero price returned by `ChainlinkPriceOracle.getAssetPrice()` is consumed by `LRTOracle.getAssetPrice()`, which is called inside `_getTotalEthInProtocol()`:

```solidity
uint256 assetER = getAssetPrice(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [3](#0-2) 

`_getTotalEthInProtocol()` feeds directly into `_updateRsETHPrice()`, which computes `newRsETHPrice` and contains the downside-protection auto-pause logic:

```solidity
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
``` [4](#0-3) 

`updateRSETHPrice()` is a public function with no access control:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [5](#0-4) 

### Impact Explanation

When a Chainlink feed goes stale (e.g., during network congestion or an oracle disruption), the last reported price — which may be significantly below the true market price — is accepted without question. An unprivileged caller then invokes `updateRSETHPrice()`. The artificially low asset price reduces `totalETHInProtocol`, which reduces `newRsETHPrice`. If the computed drop exceeds `pricePercentageLimit`, the auto-pause fires, freezing the deposit pool, withdrawal manager, and oracle simultaneously. All user deposits and withdrawals are blocked until an admin manually unpauses. This constitutes a **temporary freezing of funds** triggered by a publicly callable function with no privilege requirement.

Additionally, a stale price that is artificially high (e.g., the feed froze at a peak) inflates `totalETHInProtocol`, causing excess protocol fees to be minted to the treasury — constituting **theft of unclaimed yield**.

### Likelihood Explanation

Chainlink feeds can go stale during L2 sequencer outages, periods of low volatility (heartbeat not triggered), or oracle network disruptions. This is a known, documented failure mode. The entry path requires only a public function call with no special role. Any depositor, withdrawer, or external keeper can trigger it.

### Recommendation

Add the same three validity checks used in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```diff
 function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
     AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

-    (, int256 price,,,) = priceFeed.latestRoundData();
+    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();
+    require(answeredInRound >= roundId, "StalePrice");
+    require(updatedAt != 0, "IncompleteRound");
+    require(price > 0, "InvalidPrice");

     return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
 }
```

### Proof of Concept

1. Chainlink feed for a supported asset (e.g., stETH/ETH) goes stale — `updatedAt` is hours old, `answeredInRound < roundId`, or `answer == 0`.
2. Any unprivileged user calls `LRTOracle.updateRSETHPrice()`.
3. `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `LRTOracle.getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice(asset)`.
4. `ChainlinkPriceOracle` returns the stale/zero price with no revert.
5. `totalETHInProtocol` is computed using the corrupted price, yielding a `newRsETHPrice` far below `highestRsethPrice`.
6. The `isPriceDecreaseOffLimit` condition triggers; `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()` are called.
7. All user deposits and withdrawals are frozen until an admin intervenes. [1](#0-0) [6](#0-5) [7](#0-6)

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
