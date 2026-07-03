Audit Report

## Title
Missing Chainlink Staleness Validation Enables Excess rsETH Minting at Stale Inflated LST Price - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all staleness indicators (`updatedAt`, `answeredInRound`, `roundId`), returning the last known price unconditionally. Because `LRTDepositPool.depositAsset()` uses this price to compute rsETH minting amounts via `getRsETHAmountToMint()`, a stale inflated LST price allows any caller to receive more rsETH than the deposited asset is worth, diluting existing rsETH holders.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches price data as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values are destructured but only `price` (`answer`) is used. `updatedAt` and `answeredInRound` — the standard Chainlink staleness signals — are silently discarded. There is no `block.timestamp - updatedAt > heartbeat` check and no `answeredInRound >= roundId` check.

The call chain from public entry point to stale price consumption is:

1. `LRTDepositPool.depositAsset()` (public, permissionless, `whenNotPaused`) calls `_beforeDeposit()`
2. `_beforeDeposit()` calls `getRsETHAmountToMint(asset, depositAmount)`
3. `getRsETHAmountToMint()` computes `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`
4. `LRTOracle.getAssetPrice()` delegates to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)` — which resolves to `ChainlinkPriceOracle.getAssetPrice()`

`rsETHPrice` is a stored value updated by a separate `updateRSETHPrice()` call and is not atomically refreshed on each deposit. If the stale LST price inflates the numerator while `rsETHPrice` reflects a prior fair-price update, the attacker receives excess rsETH.

The same codebase demonstrates awareness of this pattern: `ChainlinkOracleForRSETHPoolCollateral.getRate()` checks `answeredInRound < roundID` and `timestamp == 0`, yet the primary deposit oracle applies neither check.

The SECURITY.md exclusion for "incorrect data supplied by third-party oracles" does not apply here. Chainlink is functioning as designed — it returns the last known price. The defect is in the protocol's own wrapper contract failing to validate whether that price is current.

## Impact Explanation

If a Chainlink LST/ETH feed goes stale at a price above the true market price (e.g., stETH/ETH feed last reported 1.05 while real price has dropped to 0.95 during a depeg), an attacker deposits stETH and receives rsETH computed at the inflated rate. The excess rsETH represents value extracted from existing rsETH holders, whose proportional claim on the protocol's TVL is diluted. This constitutes **theft of unclaimed yield** from existing rsETH holders.

**Impact: High** — Theft of unclaimed yield via excess rsETH minting at a stale inflated LST price.

## Likelihood Explanation

Chainlink feeds have documented heartbeat windows (typically 1 hour for LST/ETH pairs). A heartbeat miss, sequencer outage (L2), or node disruption during a volatile market event — exactly when LST depegs are most likely — creates the exploitation window. The entry point is the public, permissionless `depositAsset()` function requiring no special role. The attack is repeatable until the feed resumes or the protocol is manually paused.

**Likelihood: Medium** — Requires a Chainlink feed to go stale at an inflated price, an uncommon but historically observed event during market stress.

## Recommendation

Add staleness validation inside `ChainlinkPriceOracle.getAssetPrice()` with a per-feed heartbeat mapping:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (block.timestamp - updatedAt > heartbeat[assetPriceFeed[asset]]) revert StalePrice();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Set `heartbeat` per feed (e.g., 3600 seconds with a small buffer). This mirrors and extends the partial pattern already applied in `ChainlinkOracleForRSETHPoolCollateral`.

## Proof of Concept

1. stETH/ETH Chainlink feed last updated at `1.05e18`; `updatedAt` is now >1 hour old.
2. Real stETH price has dropped to `0.95e18` (depeg), but `latestRoundData()` still returns `1.05e18`.
3. `rsETHPrice` was last updated at `1.0e18` (fair value, prior to depeg).
4. Attacker calls `LRTDepositPool.depositAsset(stETH, 1e18, 0, "")`.
5. `getRsETHAmountToMint` computes: `rsethAmountToMint = (1e18 * 1.05e18) / 1e18 = 1.05e18`.
6. Attacker receives `1.05e18` rsETH for `1e18` stETH worth only `0.95e18` ETH — a `~0.10 ETH` profit per stETH deposited, extracted from existing rsETH holders.
7. Attack is repeatable until the feed resumes or the protocol is manually paused.

**Foundry fork test plan**: Fork mainnet, mock a Chainlink stETH/ETH aggregator returning a stale `updatedAt` (>1 hour ago) with price `1.05e18`, set `rsETHPrice` to `1e18`, call `depositAsset` with `1e18` stETH, assert minted rsETH exceeds `1e18`.