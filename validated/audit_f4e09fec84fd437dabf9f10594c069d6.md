Audit Report

## Title
Chainlink Oracle Output Not Validated for Staleness or Zero Price, Corrupting rsETH Price and Enabling Fund Loss - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and discards all validity fields (`updatedAt`, `answeredInRound`, `roundId`), accepting zero, negative, or stale prices without reversion. This unvalidated price feeds into the public `LRTOracle.updateRSETHPrice()`, which any unprivileged caller can invoke, allowing a corrupted Chainlink feed to deflate `rsETHPrice` and enable an attacker to mint excess rsETH at the expense of existing holders.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price with no validity checks:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values except `price` are silently discarded. A zero price casts to `uint256(0)`, a negative price wraps to a near-max `uint256`, and a stale price is accepted as current.

The sibling contract `ChainlinkOracleForRSETHPoolCollateral.getRate()` (L30-32) correctly validates `answeredInRound < roundID`, `timestamp == 0`, and `ethPrice <= 0`, demonstrating the protocol team is aware of the pattern but did not apply it here.

**Call chain for zero-price exploit:**
1. Chainlink feed for a supported LST returns `price = 0`
2. `ChainlinkPriceOracle.getAssetPrice(asset)` → returns `0`
3. `LRTOracle.getAssetPrice(asset)` (L157) → delegates to above
4. `LRTOracle._getTotalEthInProtocol()` (L339-343) → that asset contributes `0` to `totalETHInProtocol`
5. `LRTOracle._updateRsETHPrice()` (L250) → `newRsETHPrice = understated_totalETH / rsethSupply`
6. Downside protection at L270-282 only pauses if `pricePercentageLimit > 0` AND the drop exceeds `pricePercentageLimit.mulWad(highestRsethPrice)`. If `pricePercentageLimit == 0` (default unset state) or the affected asset is a small fraction of TVL, the check is bypassed and `rsETHPrice` is updated to the deflated value.
7. `LRTOracle.updateRSETHPrice()` (L87) is `public` with no access control beyond `whenNotPaused` — any caller can trigger this.
8. Attacker immediately calls `depositETH()` or `depositAsset()` with a healthy asset. `getRsETHAmountToMint()` (L520) computes `(amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()` — with a deflated denominator, the attacker receives far more rsETH than their deposit is worth.

**Existing guard insufficiency:** The `pricePercentageLimit` guard (L273-274) is gated on `pricePercentageLimit > 0`, meaning it is entirely inactive in the default/unset state. Even when set, a zero price for a minor LST (e.g., 3% of TVL) may fall within the configured limit, allowing the price update to proceed.

## Impact Explanation
**High — Theft of unclaimed yield.** When `rsETHPrice` is deflated below its true value, a depositor minting rsETH receives more shares than their deposit warrants. The excess rsETH represents a claim on protocol TVL that was not contributed by the attacker, directly diluting the yield and principal share of all existing rsETH holders. This matches the allowed impact "Theft of unclaimed yield."

**Medium — Temporary freezing of funds.** If a stale high price inflates `rsETHPrice` above `highestRsethPrice` beyond `pricePercentageLimit`, the upside check at L256-265 reverts for non-managers, blocking all public price updates. When the true price eventually corrects downward past the limit, the downside protection at L277-281 auto-pauses `LRTDepositPool` and `LRTWithdrawalManager`, freezing all user deposits and withdrawals until an admin manually unpauses.

## Likelihood Explanation
Chainlink feeds have documented historical incidents of returning zero or stale prices during network congestion, sequencer downtime (L2), or feed migration/deprecation. No privileged access is required: `updateRSETHPrice()` is callable by any EOA or contract. The attacker needs only to monitor for a degraded feed and call two public functions (`updateRSETHPrice()` then `depositAsset()`/`depositETH()`). The condition is repeatable whenever a feed degrades.

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

Additionally, ensure `pricePercentageLimit` is initialized to a non-zero value at deployment so the downside protection is active as a secondary defense.

## Proof of Concept
**Minimal call sequence (local fork):**

1. Deploy/fork with stETH as a supported asset using `ChainlinkPriceOracle`, representing ~10% of TVL.
2. Mock the stETH/ETH Chainlink feed to return `price = 0` from `latestRoundData()`.
3. Set `pricePercentageLimit` to 5% (1% = 1e16, so 5% = 5e16) — a 10% TVL asset going to zero causes ~10% drop, exceeding 5%, so the pause triggers. Reduce to 15% limit to allow the update through.
4. Call `LRTOracle.updateRSETHPrice()` as an unprivileged EOA — succeeds, `rsETHPrice` is now deflated.
5. Call `LRTDepositPool.depositETH{value: 1 ether}()` — `getRsETHAmountToMint` returns `(1e18 * ETH_price) / deflated_rsETHPrice`, minting excess rsETH.
6. Assert attacker's rsETH balance exceeds `1e18 * ETH_price / true_rsETHPrice`.
7. Repeat with `pricePercentageLimit = 0` (default) to demonstrate the guard is fully inactive without explicit configuration.