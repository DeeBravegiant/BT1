All code references check out. The claim is accurate and the exploit path is concrete.

Audit Report

## Title
`ChainlinkPriceOracle.getAssetPrice()` Missing Staleness and Validity Checks Enable Inflated rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` discards all `latestRoundData()` return values except the raw price, performing no staleness, incomplete-round, or non-positive-price validation. The sibling oracle `ChainlinkOracleForRSETHPoolCollateral.getRate()` enforces all three guards on the identical Chainlink call. When a supported LST feed goes stale at an inflated price, an unprivileged depositor can call `LRTDepositPool.depositAsset()` to receive excess rsETH, diluting existing holders' proportional claim on protocol TVL.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` reads only the `price` field:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol line 52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

`roundId`, `startedAt`, `updatedAt`, and `answeredInRound` are silently discarded. In contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` enforces three explicit guards:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol lines 27-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0)            revert IncompleteRound();
if (ethPrice <= 0)             revert InvalidPrice();
``` [2](#0-1) 

The unguarded price flows directly into the deposit minting calculation:

- `LRTDepositPool.getRsETHAmountToMint()` computes `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`. [3](#0-2) 

- `LRTOracle.getAssetPrice()` delegates directly to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)`, which resolves to `ChainlinkPriceOracle.getAssetPrice()`. [4](#0-3) 

- `LRTOracle._getTotalEthInProtocol()` also calls `getAssetPrice(asset)` for every supported asset, feeding the stale price into `_updateRsETHPrice()` and the stored `rsETHPrice`. [5](#0-4) 

- `LRTWithdrawalManager._createUnlockParams()` calls `lrtOracle.getAssetPrice(asset)` to determine the asset price used to settle withdrawal requests. [6](#0-5) 

The `pricePercentageLimit` guard in `_updateRsETHPrice()` only applies to the stored `rsETHPrice` update path and does not protect `getRsETHAmountToMint()`, which uses the stale asset price directly against the previously stored (correct) `rsETHPrice`. [7](#0-6) 

## Impact Explanation
When a Chainlink LST/ETH feed (e.g., stETH/ETH) goes stale at a price above the current market rate, `getRsETHAmountToMint()` computes a larger rsETH amount than the deposited collateral warrants. The excess rsETH is minted against the existing supply, diluting every current rsETH holder's proportional claim on protocol TVL. This constitutes theft of unclaimed yield from existing rsETH holders — a **High** impact within the allowed scope.

## Likelihood Explanation
Chainlink feeds go stale during network congestion, sequencer downtime, or feed deprecation. No special permissions are required: any external caller can invoke `LRTDepositPool.depositAsset()`. The attacker only needs to observe on-chain Chainlink round data (`answeredInRound < roundId`) and submit a deposit transaction during the stale window. The condition is repeatable across any supported LST feed. [1](#0-0) 

## Recommendation
Add the same guards present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0)            revert IncompleteRound();
    if (price <= 0)                revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, consider a per-feed `block.timestamp - updatedAt > heartbeat` check, as `answeredInRound < roundId` is a deprecated Chainlink staleness signal on some feed versions.

## Proof of Concept
1. A Chainlink LST/ETH feed (e.g., stETH/ETH) stops updating; on-chain state reaches `answeredInRound < roundId` with last reported price 1.05e18 (real market rate: 0.98e18).
2. `ChainlinkOracleForRSETHPoolCollateral.getRate()` reverts with `StalePrice()` — RSETHPool deposits are blocked.
3. `ChainlinkPriceOracle.getAssetPrice(stETH)` returns 1.05e18 — no revert.
4. Attacker calls `LRTDepositPool.depositAsset(stETH, amount, 0, "")`.
5. `getRsETHAmountToMint()` computes `rsethAmountToMint = amount * 1.05e18 / rsETHPrice`, minting ~7% more rsETH than the deposited collateral is worth at the real market rate.
6. Attacker holds inflated rsETH; existing holders' share of TVL is diluted by the excess minted supply.

**Foundry fork test plan**: Fork mainnet, mock a Chainlink stETH/ETH aggregator to return `answeredInRound < roundId` with an inflated price, call `depositAsset`, assert that `rsethAmountToMint` exceeds the fair value computed at the real price, and assert that existing holder TVL share decreases. [1](#0-0) [2](#0-1)

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

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
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

**File:** contracts/LRTWithdrawalManager.sol (L837-851)
```text
    function _createUnlockParams(
        ILRTOracle lrtOracle,
        ILRTUnstakingVault unstakingVault,
        address asset
    )
        internal
        view
        returns (UnlockParams memory)
    {
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
    }
```
