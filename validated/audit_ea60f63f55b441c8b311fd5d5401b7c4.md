### Title
Unchecked Negative Chainlink Price Cast to `uint256` Causes Overflow Revert, Freezing Deposits and Price Updates - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary

`ChainlinkPriceOracle.getAssetPrice()` directly casts the `int256 price` returned by Chainlink's `latestRoundData()` to `uint256` without checking for a negative value. In Solidity 0.8.x, the subsequent multiplication `uint256(price) * 1e18` overflows and reverts when `price` is negative, because `uint256(-1)` equals `type(uint256).max`. This causes all callers — including `LRTOracle.updateRSETHPrice()` and `LRTDepositPool` deposit functions — to revert, temporarily freezing deposits and price updates.

### Finding Description

In `contracts/oracles/ChainlinkPriceOracle.sol`, `getAssetPrice()` fetches the Chainlink price and immediately casts it:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

If `price` is negative (e.g., `-1`), then `uint256(-1)` = `2^256 - 1`. Multiplying this by `1e18` overflows in Solidity 0.8.x and reverts. There is no `price <= 0` guard.

By contrast, the sibling oracle `ChainlinkOracleForRSETHPoolCollateral.sol` in the same codebase correctly validates the price before casting:

```solidity
if (ethPrice <= 0) revert InvalidPrice();
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / ...
``` [2](#0-1) 

This inconsistency confirms the protocol is aware of the risk but failed to apply the guard in `ChainlinkPriceOracle`.

### Impact Explanation

`ChainlinkPriceOracle.getAssetPrice()` is consumed by `LRTOracle.getAssetPrice()`, which is called inside `_getTotalEthInProtocol()`, which is called by the public `updateRSETHPrice()` and by `LRTDepositPool` deposit flows. [3](#0-2) 

If any supported asset's Chainlink feed returns a negative price:
1. `getAssetPrice()` reverts due to overflow.
2. `updateRSETHPrice()` (public, callable by anyone) reverts — the rsETH price cannot be updated.
3. All `LRTDepositPool.depositETH()` / `depositAsset()` calls that internally rely on the oracle revert — user deposits are blocked.

This constitutes **temporary freezing of funds** (Medium severity).

### Likelihood Explanation

Chainlink acknowledges that `latestRoundData()` can return a negative `answer` in edge cases (e.g., during extreme market conditions or feed misconfiguration). While rare, it is a documented possibility. The protocol already guards against it in `ChainlinkOracleForRSETHPoolCollateral`, confirming the team considers it a realistic scenario.

### Recommendation

Add a non-positive price check before casting, consistent with the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= 0) revert InvalidPrice();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

### Proof of Concept

1. A supported LST asset's Chainlink feed (registered via `updatePriceFeedFor`) returns `price = -1`.
2. `ChainlinkPriceOracle.getAssetPrice(asset)` executes `uint256(-1) * 1e18`.
3. Solidity 0.8.x detects overflow → reverts.
4. `LRTOracle._getTotalEthInProtocol()` reverts.
5. `LRTOracle.updateRSETHPrice()` reverts — price is frozen at stale value.
6. Any user calling `LRTDepositPool.depositETH()` or `depositAsset()` that triggers oracle reads also reverts — deposits are blocked until the feed recovers or the oracle is replaced. [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L32-34)
```text
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
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
