Audit Report

## Title
Missing Chainlink Oracle Staleness Validation Allows Stale Price to Set Incorrect rsETH Exchange Rate - (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle::getAssetPrice` calls `latestRoundData()` and discards all fields except `price`, performing no staleness, completeness, or validity checks. A stale Chainlink feed price propagates through `LRTOracle::_getTotalEthInProtocol` into `rsETHPrice`, which governs how many rsETH tokens are minted per deposit. Any unprivileged caller can invoke the public `updateRSETHPrice()` to commit a stale price to storage, enabling share dilution of existing rsETH holders.

## Finding Description
`ChainlinkPriceOracle::getAssetPrice` at line 52 silently discards `roundId`, `updatedAt`, and `answeredInRound`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol line 52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No revert path exists for any staleness condition. By contrast, `ChainlinkOracleForRSETHPoolCollateral::getRate` (lines 30–32) performs `answeredInRound < roundID`, `timestamp == 0`, and `ethPrice <= 0` checks on the same interface. The core L1 asset oracle has none of these guards.

The full call chain:
1. `LRTOracle::updateRSETHPrice()` (line 87) — public, only `whenNotPaused`, no access control.
2. → `_updateRsETHPrice()` → `_getTotalEthInProtocol()` (line 331).
3. → `getAssetPrice(asset)` (line 339) → `ChainlinkPriceOracle::getAssetPrice()` → stale `latestRoundData()`.
4. Stale `assetER` multiplied into `totalETHInProtocol` (line 343).
5. `newRsETHPrice` computed at line 250 and written to `rsETHPrice` at line 313.

The `pricePercentageLimit` guard (lines 252–266, 270–291) is not a sufficient mitigation: it defaults to `0` (uninitialized), in which case `pricePercentageLimit > 0` is `false` and the check is entirely bypassed. Even when set, it only catches deviations exceeding the configured threshold, leaving sub-threshold stale prices undetected.

## Impact Explanation
**High — Theft of unclaimed yield.**

When a Chainlink feed lags behind a real price increase (stale price is lower than actual), `_getTotalEthInProtocol` understates TVL, `rsETHPrice` is set below fair value, and new depositors receive more rsETH than they are entitled to. This directly dilutes the proportional claim of existing rsETH holders on the underlying ETH — constituting theft of unclaimed yield from current holders. The impact is bounded by the magnitude of the stale deviation and the deposit volume during the stale window, but is concrete and repeatable.

## Likelihood Explanation
Chainlink stETH/ETH and similar LST feeds on Ethereum L1 have 24-hour heartbeat intervals. During low-volatility periods the feed legitimately does not update for the full heartbeat window. `updateRSETHPrice()` is callable by any external account with no privilege requirement. An attacker needs only to monitor feed `updatedAt` timestamps off-chain, wait for a feed to lag behind a real price move, and call `updateRSETHPrice()`. No admin access, no governance capture, and no victim mistake is required. The attack is repeatable every heartbeat cycle.

## Recommendation
Add staleness validation in `ChainlinkPriceOracle::getAssetPrice`, mirroring the pattern already present in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (block.timestamp - updatedAt > MAX_STALENESS) revert PriceExpired();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_STALENESS` should be configured per-feed based on the Chainlink heartbeat (e.g., 25 hours for a 24-hour heartbeat feed).

## Proof of Concept
1. Chainlink stETH/ETH feed last updated 23 hours ago at `1.05e18`. Real stETH price has since risen to `1.08e18` but the feed has not yet updated (within heartbeat window, no deviation trigger).
2. Attacker calls `LRTOracle::updateRSETHPrice()` (public, no access control).
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle::getAssetPrice(stETH)` → returns stale `1.05e18` instead of `1.08e18`.
4. TVL is understated → `newRsETHPrice` is set ~2.8% below fair value → written to `rsETHPrice` at line 313.
5. Attacker immediately calls `LRTDepositPool::depositAsset` or `depositETH`; `getRsETHAmountToMint` uses the depressed `rsETHPrice` → attacker receives ~2.8% more rsETH than fair value.
6. Existing rsETH holders' proportional ETH claim is diluted by the excess rsETH minted.

**Foundry fork test outline:**
- Fork Ethereum mainnet at a block where the stETH/ETH Chainlink feed `updatedAt` is >1 hour old but within heartbeat.
- Call `updateRSETHPrice()` and record `rsETHPrice`.
- Warp `block.timestamp` forward to simulate feed staleness while keeping `updatedAt` fixed.
- Call `updateRSETHPrice()` again; assert `rsETHPrice` diverges from the expected fair-value price.
- Deposit and assert minted rsETH exceeds the fair-value amount. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** contracts/LRTOracle.sol (L312-315)
```text

        rsETHPrice = newRsETHPrice;

        emit RsETHPriceUpdate(rsETHPrice, previousPrice);
```

**File:** contracts/LRTOracle.sol (L331-343)
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
