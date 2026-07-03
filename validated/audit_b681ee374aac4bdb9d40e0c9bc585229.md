Audit Report

## Title
Missing Chainlink Price Validation Enables Circuit-Breaker Price Exploitation in Deposits - (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and returns the raw price with no staleness check, no `price > 0` guard, and no circuit-breaker bounds validation. The sister contract `ChainlinkOracleForRSETHPoolCollateral` in the same repository performs all three checks, confirming the team is aware of these requirements. The unvalidated price flows directly into rsETH minting and rsETH price calculation, enabling an attacker to deposit a severely depegged LST at its Chainlink circuit-breaker floor price and receive rsETH minted at that inflated rate.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` (L49–55) reads the raw Chainlink price and returns it without any sanity checks:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No `updatedAt` heartbeat check, no `price > 0` guard, and no comparison against the aggregator's `minAnswer`/`maxAnswer` circuit-breaker bounds.

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` (L26–37) validates `answeredInRound < roundID`, `timestamp == 0`, and `ethPrice <= 0`, demonstrating the team is aware of these checks but omitted them from the primary oracle path used for deposits.

The unvalidated price propagates through:
- `LRTOracle.getAssetPrice()` (L156–158) → delegates directly to `IPriceFetcher.getAssetPrice()`
- `LRTDepositPool.getRsETHAmountToMint()` (L519–520): `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`
- `LRTOracle._getTotalEthInProtocol()` (L339): `uint256 assetER = getAssetPrice(asset)` — used in every `updateRSETHPrice()` call

Chainlink price feeds have hard-coded circuit breakers: when an asset's true market price falls below `minAnswer`, the feed returns `minAnswer` rather than the real price. Because the protocol performs no bounds check, it accepts the circuit-breaker floor as the true price.

## Impact Explanation
**Critical — Direct theft of user funds / Protocol insolvency.**

If a supported LST (e.g., stETH, rETH) depegs severely, Chainlink's circuit breaker clamps the reported price at `minAnswer` (e.g., 0.9 ETH) while the true market price is far lower (e.g., 0.3 ETH). An attacker deposits the cheap LST, receives rsETH minted at the inflated circuit-breaker price, and redeems rsETH for other protocol assets at fair value — extracting multiples of their deposit at the expense of honest depositors. This matches the allowed impact "Direct theft of any user funds."

Additionally, if `price` is 0 (possible during oracle failure), `uint256(price)` returns 0, causing `getRsETHAmountToMint` to return 0 and `_beforeDeposit` to revert on `rsethAmountToMint < minRSETHAmountExpected`, freezing deposits — matching "Temporary freezing of funds."

## Likelihood Explanation
**Medium.** Chainlink circuit breakers are a documented, real-world mechanism triggered during extreme market events (the LUNA collapse is the canonical example). The protocol supports multiple LSTs, each with its own Chainlink feed and circuit-breaker bounds. No privileged access is required — any depositor can call `depositAsset()`. The attacker does not need to cause the depeg; they only need to observe it and act. The SECURITY.md exclusion for "depegging of an external stablecoin" does not apply here because stETH and rETH are liquid staking tokens, not stablecoins.

## Recommendation
In `ChainlinkPriceOracle.getAssetPrice()`, after calling `latestRoundData()`, add:

1. **Staleness**: `require(updatedAt >= block.timestamp - HEARTBEAT, "Stale price")` (heartbeat configurable per feed).
2. **Negative/zero price**: `require(price > 0, "Invalid price")`.
3. **Circuit-breaker bounds**: Fetch `IChainlinkAggregator(priceFeed.aggregator()).minAnswer()` and `maxAnswer()` dynamically (not cached at construction) and assert `price > minAnswer && price < maxAnswer`.

These are already implemented in `ChainlinkOracleForRSETHPoolCollateral.getRate()` and should be mirrored in the primary oracle path.

## Proof of Concept

1. Assume stETH is a supported asset with a Chainlink feed whose `minAnswer` = 0.9e18.
2. stETH depegs to 0.3 ETH on the open market; Chainlink circuit breaker activates, feed returns 0.9e18.
3. Attacker acquires 1000 stETH for 300 ETH.
4. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
5. `_beforeDeposit` → `getRsETHAmountToMint` → `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns 0.9e18 (no validation).
6. `rsethAmountToMint = 1000e18 * 0.9e18 / rsETHPrice` — attacker receives rsETH worth ~900 ETH.
7. Attacker redeems rsETH for ~900 ETH of other assets, netting ~600 ETH profit.

**Foundry fork test plan**: Fork mainnet, mock a stETH Chainlink feed to return `minAnswer` while the spot price is 0.3e18, call `depositAsset` as an unprivileged address, assert rsETH minted exceeds fair value by the circuit-breaker ratio, and assert protocol TVL is drained relative to rsETH outstanding.