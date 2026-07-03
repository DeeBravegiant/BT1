Audit Report

## Title
Missing Staleness Validation in `ChainlinkPriceOracle.getAssetPrice` Enables Unprivileged Protocol-Wide Pause — (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice` calls `latestRoundData()` but silently discards `updatedAt` and `answeredInRound`, performing no staleness check. When any supported LST asset's Chainlink feed goes stale, the returned price is below the current market price (LSTs continuously accrue yield). Any unprivileged caller can then invoke the public `LRTOracle.updateRSETHPrice()`, causing the computed rsETH price to fall below the `pricePercentageLimit` threshold and atomically pausing `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle`, freezing all user deposits and withdrawals.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice` (L49–55) discards all round metadata:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
```

Neither `answeredInRound < roundId` (round-completeness) nor `updatedAt` (timestamp freshness) is validated. The sibling contract `ChainlinkOracleForRSETHPoolCollateral.getRate` (L30–32) already implements all three guards (`StalePrice`, `IncompleteRound`, `InvalidPrice`), confirming the protocol is aware of the requirement.

`LRTOracle._getTotalEthInProtocol` (L336–343) iterates every supported asset and calls `getAssetPrice(asset)`, which routes through `ChainlinkPriceOracle`. A stale price for any single asset understates `totalETHInProtocol`, depressing `newRsETHPrice`.

`_updateRsETHPrice` (L270–281) then evaluates the downside protection branch: if `newRsETHPrice < highestRsethPrice` and the difference exceeds `pricePercentageLimit.mulWad(highestRsethPrice)`, it calls `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()` on itself, then returns without updating state.

`updateRSETHPrice()` (L87–89) is `public whenNotPaused` with no role restriction — any EOA can call it.

## Impact Explanation

All three contracts — `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` — are paused atomically. Users cannot deposit assets or initiate/claim withdrawals until an admin with `LRTAdmin` role manually unpauses each contract. This is a **temporary freezing of funds**, matching the Medium allowed impact.

## Likelihood Explanation

Chainlink feeds go stale during network congestion or node outages. LST assets (stETH, cbETH, rETH, etc.) accrue staking yield continuously, so any stale price is always below the current market price — no manipulation is required. The required staleness duration scales inversely with `pricePercentageLimit`: at 1% (1e16) and ~5% APY, approximately 73 days of feed staleness suffices; at 0.1%, approximately 7 days. The trigger requires no capital, no privilege, and no front-running — only a public call once the staleness threshold is crossed.

## Recommendation

Add staleness validation to `ChainlinkPriceOracle.getAssetPrice`, mirroring the checks already present in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Optionally, add a configurable `heartbeat` per feed and check `block.timestamp - updatedAt > heartbeat`.

## Proof of Concept

```solidity
function testStaleOraclePausesProtocol() public {
    // Fork mainnet; warp past the Chainlink heartbeat for any supported LST feed
    // (e.g., stETH/ETH: 86400s heartbeat). No new round is published.
    vm.warp(block.timestamp + 2 days);

    // Any unprivileged caller
    vm.prank(address(0xdead));
    lrtOracle.updateRSETHPrice();

    // Stale price depresses newRsETHPrice below pricePercentageLimit threshold
    assertTrue(lrtDepositPool.paused(), "DepositPool should be paused");
    assertTrue(withdrawalManager.paused(), "WithdrawalManager should be paused");

    vm.expectRevert("Pausable: paused");
    lrtDepositPool.depositAsset(stETH, 1 ether, 0, "");
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/LRTOracle.sol (L270-281)
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
