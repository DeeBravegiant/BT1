Audit Report

## Title
Missing Staleness Validation in `ChainlinkPriceOracle.getAssetPrice()` Enables Over-Minting of rsETH - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards the `updatedAt` timestamp and `answeredInRound` fields, accepting arbitrarily stale prices without any freshness or round-completeness check. This stale price feeds directly into `LRTDepositPool.getRsETHAmountToMint()`, allowing any depositor to receive more rsETH than the fair ETH-equivalent value of their deposit whenever a Chainlink LST/ETH feed is stale with an inflated price, diluting all existing rsETH holders.

## Finding Description

**Root cause — `contracts/oracles/ChainlinkPriceOracle.sol` L52:**

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values of `latestRoundData()` are available (`roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound`), but only `answer` is used. No check on `block.timestamp - updatedAt > heartbeat` (staleness) and no check on `answeredInRound >= roundId` (round completeness) are performed.

**Contrast with the protocol's own `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` L30–32**, which already implements the correct pattern:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The protocol demonstrably knows this pattern is required; it is simply absent from the L1 deposit path oracle.

**Exploit path:**

1. A Chainlink LST/ETH feed (e.g., stETH/ETH) goes stale beyond its heartbeat. The last reported price is 1.05 ETH/stETH; the true current rate is 1.00 ETH/stETH.
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
3. `_beforeDeposit` → `getRsETHAmountToMint` → `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` returns the stale 1.05e18.
4. Minting formula: `rsethAmountToMint = (1000e18 * 1.05e18) / rsETHPrice`. With `rsETHPrice = 1e18`, attacker receives **1050 rsETH** for 1000 stETH (true ETH value: 1000 ETH).
5. The 50 rsETH surplus is backed by no real ETH. When `updateRSETHPrice()` is later called with the corrected price, the protocol TVL drops by 50 ETH, reducing `rsETHPrice` for all holders.
6. Existing rsETH holders' share of the underlying pool is permanently reduced without compensation.

**Existing guards are insufficient:**

- The `pricePercentageLimit` check in `_updateRsETHPrice()` only fires when the oracle price update is called, not at deposit time. The over-minting has already occurred before any price update.
- The `minRSETHAmountExpected` slippage parameter in `depositAsset` protects the depositor, not existing holders.
- No circuit breaker prevents `depositAsset` from executing with a stale oracle price.

## Impact Explanation

**High — Theft of unclaimed yield / dilution of existing rsETH holders.**

When the attacker redeems their over-minted rsETH after the price corrects, they extract more ETH than they deposited. The deficit is borne by all existing rsETH holders whose per-token ETH backing is permanently reduced. The magnitude scales linearly with deposit size and degree of price staleness. This is a concrete, quantifiable, repeatable extraction of value from existing holders by any unprivileged external depositor.

## Likelihood Explanation

Chainlink LST/ETH feeds operate on heartbeat models (typically 1–24 hours). During Ethereum network congestion, keeper transactions fail to land on time, causing feeds to go stale beyond their heartbeat — a historically documented condition (e.g., March 2020). No attacker action is required to cause the staleness; the attacker only needs to observe the stale state and call `depositAsset`. The attack is permissionless, requires no special role, and is repeatable across any supported LST asset whose Chainlink feed is stale.

## Recommendation

Add staleness and round-completeness checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (block.timestamp - updatedAt > MAX_PRICE_AGE) revert PriceOutdated();
if (price <= 0) revert InvalidPrice();
```

`MAX_PRICE_AGE` should be configured per-asset to match the Chainlink feed's documented heartbeat plus a reasonable buffer (e.g., heartbeat + 30 minutes).

## Proof of Concept

**Foundry fork test outline:**

```solidity
// Fork mainnet at a block where stETH/ETH Chainlink feed is stale (or mock the feed)
function testStaleOracleOverMint() public {
    // 1. Deploy mock Chainlink aggregator returning price=1.05e18, updatedAt=block.timestamp - 25 hours
    MockAggregator staleAgg = new MockAggregator(1.05e18, block.timestamp - 25 hours);
    // 2. Set as price feed for stETH in ChainlinkPriceOracle
    vm.prank(lrtManager);
    chainlinkOracle.updatePriceFeedFor(stETH, address(staleAgg));
    // 3. Record existing holder's rsETH balance and total supply
    uint256 supplyBefore = rsETH.totalSupply();
    // 4. Attacker deposits 1000 stETH
    vm.prank(attacker);
    lrtDepositPool.depositAsset(stETH, 1000e18, 0, "");
    // 5. Assert attacker received 1050 rsETH (not 1000)
    assertEq(rsETH.balanceOf(attacker), 1050e18);
    // 6. Update rsETH price with corrected feed (1.00e18)
    MockAggregator freshAgg = new MockAggregator(1.00e18, block.timestamp);
    vm.prank(lrtManager);
    chainlinkOracle.updatePriceFeedFor(stETH, address(freshAgg));
    lrtOracle.updateRSETHPrice();
    // 7. Assert rsETHPrice has dropped, diluting existing holders
    assertLt(lrtOracle.rsETHPrice(), 1e18);
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
