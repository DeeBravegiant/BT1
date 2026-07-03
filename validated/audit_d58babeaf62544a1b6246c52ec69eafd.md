Audit Report

## Title
Unvalidated Chainlink `latestRoundData()` Return Values Enable Stale Price Consumption in Deposit Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and silently discards `roundId`, `updatedAt`, and `answeredInRound`, performing no staleness or validity checks on the returned price. A stale or inflated price propagates directly into `LRTDepositPool.getRsETHAmountToMint()`, causing depositors to receive more rsETH than their collateral is worth, diluting all existing holders and driving the protocol toward insolvency.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` at line 52 discards all validation fields:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No checks are applied for `answeredInRound < roundId` (stale round), `updatedAt == 0` (incomplete round), `block.timestamp - updatedAt > heartbeat` (feed timeout), or `price <= 0` (invalid price).

The contrast with `ChainlinkOracleForRSETHPoolCollateral.getRate()` (lines 27–32) confirms the team is aware of the requirement and has applied it elsewhere:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L27-32
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The stale price flows through the full deposit path:
- `LRTDepositPool.depositAsset()` → `_beforeDeposit()` → `getRsETHAmountToMint()` (line 520): `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`
- `LRTOracle.getAssetPrice()` (line 157): `return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)` — delegates directly to `ChainlinkPriceOracle.getAssetPrice()`

The `minRSETHAmountExpected` slippage guard in `_beforeDeposit()` (line 667) does not protect against receiving *more* rsETH than deserved; it only protects the depositor from receiving *less*. No other guard in the deposit path validates oracle freshness.

## Impact Explanation
**Protocol insolvency (Critical).** If the Chainlink feed is stale and the last recorded price exceeds the true current price (e.g., an LST depegs after the last oracle update), every depositor calling `depositAsset` or `depositETH` receives more rsETH than the deposited collateral is worth. This dilutes all existing rsETH holders proportionally. If the discrepancy is large or the condition persists, the total rsETH supply becomes unbacked, constituting protocol insolvency — a directly allowed Critical impact.

Additionally, if `price` returns `0` (uninitialised or broken round), `rsethAmountToMint` evaluates to `0`. A depositor who sets `minRSETHAmountExpected = 0` will have their assets transferred in but receive zero rsETH — a permanent freeze of deposited funds, also a Critical impact.

## Likelihood Explanation
Chainlink feeds go stale during chain congestion, oracle node failures, or when a pegged asset's price barely moves and the deviation threshold is never crossed. These conditions are well-documented and have occurred on Ethereum mainnet. No special attacker capability is required: any depositor transacting during a staleness window is affected automatically. The condition is externally triggered (oracle infrastructure), not dependent on any privileged action.

## Recommendation
Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral.getRate()` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
if (block.timestamp - updatedAt > HEARTBEAT) revert StalePrice();
```

`HEARTBEAT` should be configured per feed (e.g., 3600 s for a 1-hour feed, 86 400 s for a 24-hour feed), stored as an immutable or mapping set at oracle registration time.

## Proof of Concept

1. A Chainlink LST/ETH feed goes stale (e.g., last update was 4 hours ago for a 1-hour heartbeat feed). Last recorded price: `1.05e18`; true current price: `1.00e18`.
2. Attacker (or any depositor) calls `LRTDepositPool.depositAsset(asset, 1000e18, 0, "")`.
3. `_beforeDeposit` → `getRsETHAmountToMint(asset, 1000e18)` → `lrtOracle.getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice(asset)` returns stale `1.05e18`.
4. `rsethAmountToMint = (1000e18 * 1.05e18) / rsETHPrice` — depositor receives 5% more rsETH than collateral justifies.
5. No revert; no staleness check exists in `ChainlinkPriceOracle`.
6. Existing rsETH holders are diluted; repeated deposits widen the undercollateralisation gap until the protocol is insolvent.

**Foundry fork test plan:** Fork mainnet, mock a Chainlink aggregator returning a stale `updatedAt` (e.g., `block.timestamp - 7200`) with an inflated price. Call `depositAsset` and assert that `rsethAmountToMint` exceeds the fair value, and that no revert occurs. Confirm the same call reverts after applying the recommended fix.