Audit Report

## Title
Chainlink Oracle Output Not Validated for Staleness or Zero Price, Corrupting rsETH Price and Enabling Fund Loss - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` without validating the returned price for zero/negative values or staleness. This unvalidated price feeds into `LRTOracle._updateRsETHPrice()`, which is callable by any unprivileged user via the public `updateRSETHPrice()`. Depending on the configured `pricePercentageLimit`, a degraded feed can either corrupt `rsETHPrice` to enable theft of unclaimed yield from existing rsETH holders, or trigger an unintended protocol-wide pause that temporarily freezes all user funds.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` discards all validity-relevant return values from `latestRoundData()`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

`roundId`, `updatedAt`, and `answeredInRound` are all silently dropped. A zero or negative `price` is cast to `uint256(0)` or wraps, and a stale price is accepted as current. The sibling contract `ChainlinkOracleForRSETHPoolCollateral.getRate()` (L26-37) in the same repository correctly validates all three conditions (`answeredInRound < roundID`, `timestamp == 0`, `ethPrice <= 0`), confirming the pattern is known and intentionally applied elsewhere.

The unvalidated price propagates through:
1. `ChainlinkPriceOracle.getAssetPrice(asset)` → returns bad price
2. `LRTOracle.getAssetPrice(asset)` (L156-158) → delegates to above
3. `LRTOracle._getTotalEthInProtocol()` (L331-349) → sums `totalAssetAmt * assetER` for all supported LSTs
4. `LRTOracle._updateRsETHPrice()` (L214-316) → computes `newRsETHPrice = (totalETHInProtocol - fee) / rsethSupply`
5. `LRTOracle.updateRSETHPrice()` (L87-89) → **public, no access control**

The downside protection at L270-281 checks whether `newRsETHPrice` dropped more than `pricePercentageLimit` below `highestRsethPrice`. If the drop exceeds the limit, the protocol pauses and returns early without updating `rsETHPrice`. If the drop is within the limit (or `pricePercentageLimit == 0`), `rsETHPrice` is written to the deflated value at L313.

`LRTDepositPool.getRsETHAmountToMint()` (L506-521) then computes:
```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
With a deflated `rsETHPrice` denominator, any depositor of a non-affected asset receives more rsETH than their deposit is worth.

## Impact Explanation
**High — Theft of unclaimed yield:** When `pricePercentageLimit` is zero (unset) or the affected asset represents a small enough fraction of TVL that the price drop stays within the configured limit, `rsETHPrice` is updated to the deflated value. An attacker who immediately deposits ETH or an unaffected LST receives `(amount * assetPrice) / deflated_rsETHPrice` rsETH — more than their deposit is worth — diluting all existing rsETH holders. The extracted value equals the difference between the true and deflated rsETH price multiplied by the attacker's deposit amount.

**Medium — Temporary freezing of funds:** When `pricePercentageLimit > 0` and the price drop from a zero/stale price exceeds the limit, `_updateRsETHPrice()` calls `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()` (L277-281), then returns without updating `rsETHPrice`. Any unprivileged caller can trigger this by calling `updateRSETHPrice()` during a feed outage, freezing all deposits and withdrawals until an admin manually unpauses.

## Likelihood Explanation
Chainlink feeds have historically returned stale data during network congestion, sequencer downtime on L2s, or feed deprecation events. `updateRSETHPrice()` is permissionless — no role check, no access control. The combination of a degraded feed and a public price-update entry point means no privileged access is required. The attacker only needs to observe a feed degradation and call one public function. The theft path is repeatable as long as the feed remains degraded and the protocol is not paused.

## Recommendation
Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > MAX_STALENESS_PERIOD) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Define `MAX_STALENESS_PERIOD` as a per-feed configurable constant (e.g., 3600 seconds for ETH/USD, 86400 for less-frequent feeds).

## Proof of Concept
**Theft of unclaimed yield (requires `pricePercentageLimit == 0` or small-TVL asset):**

1. Deploy a mock Chainlink aggregator for stETH that returns `price = 0`.
2. Configure `ChainlinkPriceOracle` to use this mock feed for stETH.
3. Ensure `pricePercentageLimit == 0` in `LRTOracle` (or use an asset representing <`pricePercentageLimit` fraction of TVL).
4. Call `LRTOracle.updateRSETHPrice()` as an unprivileged EOA.
5. Inside `_getTotalEthInProtocol()`, `getAssetPrice(stETH)` returns `0`; stETH TVL contributes `0` to `totalETHInProtocol`.
6. `newRsETHPrice = understated_totalETH / rsethSupply` — far below true value.
7. Since `pricePercentageLimit == 0`, `isPriceDecreaseOffLimit = false`; `rsETHPrice` is updated to the deflated value.
8. Attacker calls `depositETH()` with `minRSETHAmountExpected = 0`. `getRsETHAmountToMint` returns `(msg.value * 1e18) / deflated_rsETHPrice` — significantly more rsETH than the deposit is worth.
9. All existing rsETH holders are diluted by the difference.

**Temporary freeze (requires `pricePercentageLimit > 0` and large price drop):**

1. Same setup but with `pricePercentageLimit = 1e16` (1%).
2. stETH represents >1% of TVL.
3. Call `LRTOracle.updateRSETHPrice()` as an unprivileged EOA.
4. `newRsETHPrice` drops >1% below `highestRsethPrice`; `isPriceDecreaseOffLimit = true`.
5. `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `LRTOracle._pause()` are called.
6. All deposits and withdrawals are frozen until admin intervention.