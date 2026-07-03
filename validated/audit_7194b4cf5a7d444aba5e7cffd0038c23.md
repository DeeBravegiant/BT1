All code references are confirmed accurate. The vulnerability is valid.

Audit Report

## Title
Missing Chainlink Staleness Check Enables Permissionless Auto-Pause of Protocol — (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards all freshness fields (`updatedAt`, `answeredInRound`), accepting stale prices without revert. Because `LRTOracle.updateRSETHPrice()` is a public, permissionless function, any caller can invoke it during a Chainlink feed outage, causing `_updateRsETHPrice()` to compute a depressed `newRsETHPrice` that breaches `pricePercentageLimit` relative to `highestRsethPrice`, atomically pausing `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle`.

## Finding Description
**Root cause — `ChainlinkPriceOracle.getAssetPrice()` (lines 49–55):**
```solidity
(, int256 price,,,) = priceFeed.latestRoundData();   // updatedAt / answeredInRound silently dropped
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

The same codebase's `ChainlinkOracleForRSETHPoolCollateral.getRate()` correctly validates both `answeredInRound < roundID` and `timestamp == 0`, confirming the omission in `ChainlinkPriceOracle` is a defect, not an intentional design choice. [2](#0-1) 

**Permissionless trigger — `updateRSETHPrice()` has no role guard:**
```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [3](#0-2) 

**Auto-pause logic in `_updateRsETHPrice()` (lines 270–282):** When `newRsETHPrice` falls below `highestRsethPrice` by more than `pricePercentageLimit`, all three contracts are paused atomically and the function returns early without updating `rsETHPrice`. [4](#0-3) 

**Full call chain:** `updateRSETHPrice()` → `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `getAssetPrice(asset)` → `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice()` → stale `latestRoundData()` price accepted → depressed `newRsETHPrice` → auto-pause fires. [5](#0-4) 

Existing guards are insufficient: `whenNotPaused` only blocks calls after a pause is already active; it provides no protection against the stale-price trigger itself.

## Impact Explanation
When the auto-pause fires, `LRTDepositPool.pause()`, `LRTWithdrawalManager.pause()`, and `LRTOracle._pause()` are all called atomically. All user deposits and withdrawals are blocked until an admin manually calls `unpause()` on each contract. This constitutes **temporary freezing of funds**, matching the Medium impact scope. No principal is lost, but user funds are inaccessible for an indeterminate period.

## Likelihood Explanation
- LST/LRT Chainlink feeds (stETH/ETH, rETH/ETH, ETHx/ETH) have 24-hour heartbeats and 0.5–1% deviation thresholds. A feed missing even one heartbeat returns a days-old price.
- LST prices monotonically increase (staking rewards accrue continuously), so any stale price is necessarily lower than the current `highestRsethPrice`.
- With `pricePercentageLimit` set to 1% (1e16, the value documented in the code comments at line 122), a feed stale by ~3–7 days is sufficient to breach the threshold given typical LST appreciation rates (~4–5% APY).
- The attacker requires no capital, no approvals, and no privileged role — only the ability to call a public function at the right moment.
- Chainlink feed staleness is a known, non-negligible operational risk (network congestion, node outages, L2 sequencer downtime). [6](#0-5) 

## Recommendation
Add staleness and validity checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();
require(answeredInRound >= roundId, "Stale price");
require(updatedAt != 0, "Incomplete round");
require(block.timestamp - updatedAt <= STALENESS_THRESHOLD, "Price too old");
require(price > 0, "Non-positive price");
```

`STALENESS_THRESHOLD` should be set per-feed based on its documented heartbeat (e.g., 25 hours for a 24-hour heartbeat feed). Consider storing it alongside `assetPriceFeed` in the mapping.

## Proof of Concept
1. Deploy a mock Chainlink feed returning a price 2% below the current live price with `updatedAt = block.timestamp - 48 hours`.
2. As `onlyLRTManager`, call `chainlinkOracle.updatePriceFeedFor(asset, address(mockFeed))` — this is normal operational configuration, not an attacker action.
3. As `address(0xdead)` (unprivileged), call `lrtOracle.updateRSETHPrice()`.
4. `_updateRsETHPrice()` computes `newRsETHPrice` ~2% below `highestRsethPrice`; with `pricePercentageLimit = 1e16` (1%), `isPriceDecreaseOffLimit` is `true`.
5. Assert `lrtDepositPool.paused() == true`, `withdrawalManager.paused() == true`, `lrtOracle.paused() == true`.

The attacker's only action is step 3 — a public call with no role requirement. The mock feed setup in step 2 models the natural condition of a stale Chainlink feed and requires no attacker privilege.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-32)
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

**File:** contracts/LRTOracle.sol (L122-127)
```text
    /// @dev PricePercentageLimit for 1% is 1e16
    /// @dev Price Percentage Limit for 100% is 1e18
    /// @param _pricePercentageLimit price percentage limit
    function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
        pricePercentageLimit = _pricePercentageLimit;
        emit PricePercentageLimitUpdate(_pricePercentageLimit);
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
