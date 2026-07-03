Audit Report

## Title
Chainlink Price Feed Staleness Not Validated in `ChainlinkPriceOracle`, Enabling Deposits at Inflated LST Prices - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards `updatedAt`, `answeredInRound`, and `roundId`, performing no staleness validation. A stale Chainlink LST/ETH price flows directly into rsETH minting math via `LRTDepositPool.getRsETHAmountToMint()`, allowing a depositor to receive excess rsETH during a price feed lag, permanently diluting all existing rsETH holders when the oracle eventually corrects.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` at L49–55 destructures `latestRoundData()` as `(, int256 price,,,)`, silently discarding `roundId`, `startedAt`, `updatedAt`, and `answeredInRound`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52
(, int256 price,,,) = priceFeed.latestRoundData();
```

No round-based check (`answeredInRound >= roundId`) and no time-based check (`block.timestamp - updatedAt <= MAX_STALENESS`) are performed. By contrast, the sibling contract `ChainlinkOracleForRSETHPoolCollateral.getRate()` at L27–32 does perform both a round-based and a zero-timestamp check before returning a price.

The stale price propagates through the following confirmed call chain:

1. `LRTDepositPool.depositAsset()` (L99–118) calls `_beforeDeposit()` (L648–670).
2. `_beforeDeposit()` calls `getRsETHAmountToMint(asset, depositAmount)` (L665).
3. `getRsETHAmountToMint()` (L506–521) computes `(amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`.
4. `lrtOracle.getAssetPrice(asset)` delegates to `ChainlinkPriceOracle.getAssetPrice()`, returning the unvalidated stale price.

If a supported LST (e.g., stETH) depegs while the Chainlink feed has not yet updated within its heartbeat window, `getAssetPrice()` returns the pre-depeg (inflated) price. The numerator of the minting formula is inflated, so the attacker receives more rsETH than the true ETH value of their deposit warrants. When `updateRSETHPrice()` is next called, `rsETHPrice` is recalculated downward to reflect the true (lower) LST value, permanently encoding the dilution into the exchange rate for all prior holders.

Existing guards do not mitigate this: `minRSETHAmountExpected` is a depositor-side slippage parameter that protects the attacker, not existing holders. The deposit limit check only enforces a quantity cap, not a price sanity check.

## Impact Explanation
Existing rsETH holders suffer theft of unclaimed yield. The attacker's excess rsETH is minted against the pool's existing TVL; when the oracle corrects, the rsETH price drops proportionally, reducing the ETH value of every existing holder's position by the dilution amount. This maps directly to **High — Theft of unclaimed yield** in the allowed impact scope.

## Likelihood Explanation
Chainlink LST/ETH feeds (e.g., stETH/ETH) have heartbeat periods up to 24 hours and a 0.5% deviation threshold. During market stress — precisely when LST depegs occur — network congestion can delay oracle updates well beyond the heartbeat. No special permissions are required; any unprivileged depositor can call `depositAsset()`. The 2022 stETH depeg is a historical precedent for exactly this scenario. Likelihood: **Medium**.

## Recommendation
Add both a round-based and a time-based staleness check in `ChainlinkPriceOracle.getAssetPrice()`, consistent with the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (block.timestamp - updatedAt > MAX_STALENESS_PERIOD) revert StalePrice();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_STALENESS_PERIOD` should be set per feed based on its documented heartbeat (e.g., 25 hours for a 24-hour heartbeat feed).

## Proof of Concept
1. Assume stETH/ETH Chainlink feed has a 24-hour heartbeat. Last update was 23 hours ago at `1.0e18`. stETH depegs to `0.95e18` on-chain but the feed has not yet updated.
2. `rsETHPrice` was last stored at `1.01e18` (reflecting the pre-depeg state).
3. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
4. `getRsETHAmountToMint` computes: `(1000e18 × 1.0e18) / 1.01e18 ≈ 990e18 rsETH`.
5. True fair value at `0.95e18`: `(1000e18 × 0.95e18) / 1.01e18 ≈ 940e18 rsETH`.
6. Attacker receives ~50 rsETH excess (~5% of deposit).
7. When `updateRSETHPrice()` is next called, `rsETHPrice` drops to reflect the true LST value, permanently encoding the dilution. All prior rsETH holders' positions are worth proportionally less ETH.

**Foundry fork test plan**: Fork mainnet at a block where the stETH/ETH Chainlink feed is near its heartbeat boundary. Warp `block.timestamp` forward by 1 hour (past the last `updatedAt` but within the heartbeat). Call `depositAsset(stETH, 1000e18, 0, "")` and assert that `rsethAmountToMint` exceeds the fair-value amount computed using the true spot price. Then call `updateRSETHPrice()` with a mocked oracle returning the true price and assert that `rsETHPrice` decreases, confirming dilution of prior holders.