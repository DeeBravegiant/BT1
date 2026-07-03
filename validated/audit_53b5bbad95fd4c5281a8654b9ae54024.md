Audit Report

## Title
Unvalidated Zero Price from Chainlink Feed Enables Anyone to Trigger Protocol-Wide Pause — (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` performs no validation that the Chainlink `answer` is greater than zero. A feed returning `0` propagates through `LRTOracle._getTotalEthInProtocol()`, deflating `newRsETHPrice`. When `pricePercentageLimit` is configured, any public caller can invoke `updateRSETHPrice()` to atomically pause `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle`, freezing all user deposits and withdrawals until an admin manually unpauses.

## Finding Description

**Root cause — no price validation in `ChainlinkPriceOracle.getAssetPrice()`:**

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

There is no `price > 0` guard. A feed returning `0` causes the function to return `0` without reverting. [1](#0-0) 

**Propagation through `_getTotalEthInProtocol()`:**

`getAssetPrice(asset)` is called for each supported asset. A zero return zeroes out that asset's entire ETH contribution via `totalAssetAmt.mulWad(0) == 0`, deflating `totalETHInProtocol`. [2](#0-1) 

**Automatic pause trigger in `_updateRsETHPrice()`:**

When `newRsETHPrice` drops more than `pricePercentageLimit` below `highestRsethPrice`, all three contracts are paused atomically and the function returns early without writing the new price to storage. [3](#0-2) 

**Public entrypoint — no privilege required:**

`updateRSETHPrice()` is `public whenNotPaused`, so any EOA or contract can call it once the feed transiently returns `0`. [4](#0-3) 

**Existing guards are insufficient:**

The only guard on the pause path is `pricePercentageLimit > 0`. Once an admin sets a non-zero limit (the intended production configuration), the guard becomes the attack enabler rather than a protection. There is no staleness check, no `price > 0` check, and no minimum price floor anywhere in the oracle path. [5](#0-4) 

## Impact Explanation

**Medium — Temporary freezing of funds.**

All user deposits (`LRTDepositPool`) and withdrawals (`LRTWithdrawalManager`) are frozen until an admin calls `unpause()` on each contract. No funds are lost or stolen; the deflated price is not written to storage (the function returns early at line 281). The freeze is recoverable by admin action, placing this squarely in the "Temporary freezing of funds" category. [6](#0-5) 

## Likelihood Explanation

Chainlink feeds can return `answer = 0` in documented edge cases: newly deployed feeds before the first round, circuit-breaker conditions, or depegged assets. The missing `price > 0` guard is a well-known Chainlink integration anti-pattern. The precondition — `pricePercentageLimit > 0` — is the intended production configuration, not an unusual state. Once the feed transiently returns `0`, any public caller can race to invoke `updateRSETHPrice()` and freeze the protocol. No privileged access is required. Likelihood is **Low-Medium**: requires an external feed anomaly, but the exploit itself requires zero privilege. [4](#0-3) 

## Recommendation

Add a non-zero price guard in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= 0) revert InvalidPrice();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Additionally, add a staleness check using `updatedAt` and consider a minimum price floor to harden against other Chainlink edge cases. [7](#0-6) 

## Proof of Concept

```solidity
function test_zeroPricePausesProtocol() public {
    // 1. Deploy mock Chainlink feed returning answer = 0
    MockChainlinkFeed mockFeed = new MockChainlinkFeed(0);
    // 2. Set the feed for a supported asset (e.g., stETH)
    vm.prank(lrtManager);
    chainlinkOracle.updatePriceFeedFor(stETH, address(mockFeed));
    // 3. Ensure pricePercentageLimit is non-zero (e.g., 1% = 1e16)
    vm.prank(lrtAdmin);
    lrtOracle.setPricePercentageLimit(1e16);
    // 4. Any unprivileged caller invokes updateRSETHPrice
    vm.prank(address(0xdead));
    lrtOracle.updateRSETHPrice();
    // 5. All three contracts are now paused
    assertTrue(lrtDepositPool.paused());
    assertTrue(lrtWithdrawalManager.paused());
    assertTrue(lrtOracle.paused());
    // 6. Deposits revert
    vm.expectRevert();
    lrtDepositPool.depositAsset(stETH, 1 ether, 0, "");
}
```

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
