Audit Report

## Title
Unhandled Revert in `ChainlinkPriceOracle.getAssetPrice()` on Deprecated Feed Freezes Deposits and Pending Withdrawals - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `priceFeed.latestRoundData()` with no `try/catch` or error handling. When Chainlink deprecates a price feed by setting its underlying aggregator to `address(0)`, `latestRoundData()` reverts with empty revert data. This bare revert propagates uncaught through `LRTOracle.getAssetPrice()` and `_getTotalEthInProtocol()`, blocking all protocol functions that depend on asset pricing until an admin replaces the feed.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` directly invokes `latestRoundData()` with no protection:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

`LRTOracle.getAssetPrice()` delegates directly to the price fetcher with no protection: [2](#0-1) 

`LRTOracle._getTotalEthInProtocol()` iterates over all supported assets and calls `getAssetPrice()` for each — a single deprecated feed causes the entire loop to revert: [3](#0-2) 

The revert propagates into all user-facing functions that depend on asset pricing:

- **`LRTDepositPool.getRsETHAmountToMint()`** (called by `depositETH` and `depositAsset`): [4](#0-3) 

- **`LRTWithdrawalManager.getExpectedAssetAmount()`** (called by `initiateWithdrawal` and `instantWithdrawal`): [5](#0-4) 

- **`LRTWithdrawalManager._createUnlockParams()`** (called by `unlockQueue`): [6](#0-5) 

Note: `completeWithdrawal` uses the already-stored `request.expectedAssetAmount` and does not call `getAssetPrice`, so users with already-unlocked requests are unaffected. However, users whose requests are queued but not yet unlocked are frozen because `unlockQueue` cannot execute.

## Impact Explanation
**Medium — Temporary freezing of funds.** Users with pending (locked) withdrawal requests cannot have their requests processed via `unlockQueue`, blocking their path to `completeWithdrawal`. New deposits and withdrawal initiations are also blocked. The freeze persists until an admin calls `updatePriceOracleFor()` to replace the deprecated feed. Because admin remediation is possible, the freeze is temporary rather than permanent.

## Likelihood Explanation
No attacker action is required. Chainlink has a documented and observed practice of deprecating feeds by setting the aggregator to `address(0)` (confirmed on Polygon mainnet, January 2023). The protocol uses `ChainlinkPriceOracle` for LST assets (stETH, ETHx, rETH, etc.). Deprecation of any single registered feed triggers the freeze passively. The condition is realistic, externally triggered, and repeatable.

## Recommendation
Wrap the `latestRoundData()` call in a `try/catch` in `ChainlinkPriceOracle.getAssetPrice()` and revert with a descriptive custom error so callers can distinguish oracle failure from other errors:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    try priceFeed.latestRoundData() returns (uint80, int256 price, uint256, uint256, uint80) {
        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    } catch {
        revert OracleCallFailed();
    }
}
```

Additionally, `LRTOracle._getTotalEthInProtocol()` could wrap each per-asset `getAssetPrice()` call in a `try/catch` so that a single deprecated feed does not freeze pricing for all other assets.

## Proof of Concept
1. Chainlink deprecates the stETH/ETH feed by setting its aggregator to `address(0)`.
2. Any call to `ChainlinkPriceOracle.getAssetPrice(stETH)` triggers `latestRoundData()` on the deprecated feed, which reverts with empty revert data.
3. `LRTOracle._getTotalEthInProtocol()` calls `getAssetPrice(stETH)` inside its loop and reverts entirely.
4. `LRTDepositPool.depositETH()` → `_beforeDeposit()` → `getRsETHAmountToMint()` → `lrtOracle.getAssetPrice()` reverts. All deposits are blocked.
5. `LRTWithdrawalManager.initiateWithdrawal()` → `getExpectedAssetAmount()` → `lrtOracle.getAssetPrice()` reverts. All new withdrawal initiations are blocked.
6. `LRTWithdrawalManager.unlockQueue()` → `_createUnlockParams()` → `lrtOracle.getAssetPrice()` reverts. Queued withdrawal requests cannot be unlocked; users with pending requests cannot reach `completeWithdrawal`.
7. The freeze persists until an admin calls `updatePriceOracleFor()` to replace the deprecated Chainlink feed.

**Foundry fork test plan**: Fork mainnet, call `ChainlinkPriceOracle.updatePriceFeedFor(stETH, deprecatedFeedAddress)` as manager (where `deprecatedFeedAddress` is a mock that reverts on `latestRoundData()`), then assert that `depositETH`, `initiateWithdrawal`, and `unlockQueue` all revert.

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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L590-594)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
    }
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
