Audit Report

## Title
Missing Chainlink Staleness Validation in `getAssetPrice()` Enables Deposit at Stale Inflated Price - (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validity fields, consuming the raw price with no staleness or completeness check. This contrasts with `ChainlinkOracleForRSETHPoolCollateral`, which correctly validates `answeredInRound`, `timestamp`, and price sign. Because `depositAsset()` prices every deposit through this oracle, an attacker can deposit a depegged LST during a stale-feed window and receive excess rsETH, diluting all existing rsETH holders.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` at line 52 silently discards `roundId`, `startedAt`, `updatedAt`, and `answeredInRound`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No check exists for `answeredInRound >= roundId` (round completeness), `updatedAt != 0` (round started), or `block.timestamp - updatedAt <= heartbeat` (freshness). The same codebase's `ChainlinkOracleForRSETHPoolCollateral.getRate()` (lines 30–32) performs all three checks and reverts on failure.

The stale price propagates through a fully public call chain:
- `LRTOracle.getAssetPrice(asset)` delegates to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)` (line 157), which resolves to `ChainlinkPriceOracle`.
- `LRTOracle._getTotalEthInProtocol()` calls `getAssetPrice(asset)` for every supported LST (line 339).
- `LRTDepositPool.getRsETHAmountToMint()` computes `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()` (line 520).
- `LRTDepositPool.depositAsset()` → `_beforeDeposit()` → `getRsETHAmountToMint()` (line 111), making every deposit priced against the potentially stale feed.

No existing guard in `depositAsset()` or `_beforeDeposit()` checks oracle freshness; the only protection is `minRSETHAmountExpected`, which the attacker sets to 0.

## Impact Explanation
**High — Theft of unclaimed yield.**

When a supported LST (e.g., stETH) depegs but the Chainlink feed is stale at the pre-depeg price `P_stale > P_actual`, an attacker deposits `amount` of the depegged LST and receives `amount × P_stale / rsETHPrice` rsETH — more than the LST's actual ETH value warrants. The excess rsETH is a direct, immediate dilution of all existing rsETH holders' proportional claim on the underlying ETH pool. When `updateRSETHPrice()` is subsequently called, `_getTotalEthInProtocol()` computes TVL using the same stale price, potentially setting `rsETHPrice` incorrectly and triggering the downside-protection pause (lines 270–281) once the corrected price is observed, temporarily freezing the protocol as a secondary effect.

## Likelihood Explanation
**Medium.**

Chainlink feeds go stale in documented, recurring scenarios: L2 sequencer downtime (the protocol deploys on multiple L2s), network congestion, and node operational issues. The attack window is bounded by the feed's heartbeat (1–24 hours for LST/ETH feeds). An attacker monitoring on-chain `updatedAt` timestamps can detect staleness and execute `depositAsset()` atomically. No privileged access is required — `depositAsset()` is open to any user with no role restriction.

## Recommendation
Mirror the validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > MAX_HEARTBEAT) revert PriceTooOld();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_HEARTBEAT` should be configurable per asset to match each feed's documented heartbeat.

## Proof of Concept
1. Monitor the stETH/ETH Chainlink feed on-chain; wait until `updatedAt` is more than one heartbeat old while the actual stETH price has dropped to `P_actual < P_stale`.
2. Call `LRTDepositPool.depositAsset(stETH, amount, 0, "")`.
3. Internally, `getRsETHAmountToMint(stETH, amount)` calls `ChainlinkPriceOracle.getAssetPrice(stETH)`, which returns `P_stale` without reverting.
4. Attacker receives `amount × P_stale / rsETHPrice` rsETH — more than `amount × P_actual / rsETHPrice`.
5. Attacker initiates withdrawal via `LRTWithdrawalManager`, redeeming the excess rsETH for ETH at the expense of existing rsETH holders.

Foundry fork test: fork mainnet/L2 at a block where a target LST feed's `updatedAt` is beyond its heartbeat, mock or use the live stale feed, call `depositAsset` with the depegged LST, assert that `rsethAmountToMint > amount × P_actual / rsETHPrice`.