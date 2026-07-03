Audit Report

## Title
No Staleness Check on Chainlink Price Data Allows Stale Asset Prices to Corrupt rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all staleness-related return values, silently accepting stale prices. A stale Chainlink LST/ETH feed propagates an incorrect asset price into `rsETHPrice` via the public `updateRSETHPrice()` entry point. In the inflated-price direction, this triggers phantom fee minting to the treasury, constituting theft of unclaimed yield from existing rsETH holders.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` (L52) destructures only `answer` from `latestRoundData()`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
```

`roundId`, `startedAt`, `updatedAt`, and `answeredInRound` are all discarded. No round-completeness check (`answeredInRound < roundId`) and no time-based heartbeat check (`block.timestamp - updatedAt > heartbeat`) are performed.

The stale price flows: `ChainlinkPriceOracle.getAssetPrice()` → `LRTOracle.getAssetPrice()` (L156–158) → `_getTotalEthInProtocol()` (L339–343) → `_updateRsETHPrice()` (L231, L250). The public entry point `updateRSETHPrice()` (L87–89) has no access control beyond the pause check, so any external caller can commit the stale price to state.

In the inflated-stale-price scenario: `totalETHInProtocol` is overstated, `rewardAmount = totalETHInProtocol - previousTVL` is inflated with phantom yield, and `protocolFeeInETH` is computed on that phantom yield (L244–246). This causes rsETH to be minted to the treasury (L306) at the expense of existing rsETH holders, whose proportional share is diluted.

The `pricePercentageLimit` guard (L256–265) provides partial mitigation only when non-zero and only reverts for non-manager callers on upward price deviations above the threshold; it does not prevent the stale price from being used when the deviation is within the limit or when `pricePercentageLimit == 0` (the default uninitialized value).

The project demonstrably knows the staleness pattern: `ChainlinkOracleForRSETHPoolCollateral.getRate()` (L30–32) checks both `answeredInRound < roundID` and `timestamp == 0`, but these checks are absent from the primary oracle used for all LST assets.

## Impact Explanation
**High. Theft of unclaimed yield.** When a Chainlink LST/ETH feed returns a stale inflated price, `_updateRsETHPrice()` computes phantom protocol fees and mints rsETH to the treasury. This dilutes existing rsETH holders' proportional claim on the underlying ETH — yield that belongs to holders is instead captured by the treasury. The `rsETHPrice` written to state is also incorrect, causing all subsequent deposits and withdrawals to execute at a wrong exchange rate until the next valid price update.

## Likelihood Explanation
Chainlink feeds have historically gone stale during network congestion, sequencer downtime (on L2s), or oracle node failures. The affected oracle is the primary price source for every supported LST asset. `updateRSETHPrice()` is callable by any unprivileged external account with no cooldown, meaning a stale price can be committed to state immediately and repeatedly once a feed goes stale. No keeper or admin action is required to trigger the impact.

## Recommendation
Add round-completeness and time-based heartbeat checks in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > MAX_HEARTBEAT) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_HEARTBEAT` should be configured per feed (e.g., 1 hour for ETH/USD, 24 hours for LST/ETH feeds on Ethereum mainnet), matching each feed's documented heartbeat.

## Proof of Concept
1. Deploy a mock `AggregatorV3Interface` that returns a fixed stale price with `updatedAt = block.timestamp - 48 hours` and `answeredInRound == roundId` (so even the weak round check would pass).
2. Register the mock feed via `ChainlinkPriceOracle.updatePriceFeedFor()` for a supported LST asset.
3. Call `LRTOracle.updateRSETHPrice()` from an unprivileged EOA.
4. Observe that `rsETHPrice` is updated using the stale price with no revert.
5. If the stale price is inflated relative to `previousTVL / rsethSupply`, observe that rsETH is minted to the treasury address via `IRSETH.mint()` (L306), diluting existing holders.

Foundry fork test plan: fork Ethereum mainnet, advance `block.timestamp` by 48 hours without advancing the Chainlink round, call `updateRSETHPrice()`, and assert that `rsETHPrice` deviates from the correct value and that treasury rsETH balance increased due to phantom fee minting.