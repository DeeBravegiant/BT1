Audit Report

## Title
Stale Chainlink Price Accepted Without Staleness Validation Enables Incorrect rsETH Minting and Withdrawal Amounts - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validity fields (`updatedAt`, `answeredInRound`, `roundId`), accepting whatever price the feed last stored with no temporal or sanity check. This stale price propagates directly into rsETH mint calculations and withdrawal amount calculations, allowing a depositor to receive more rsETH than the protocol's actual TVL supports when a feed is stale at an inflated value, diluting existing rsETH holders.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` at line 52 retains only `price` from `latestRoundData()`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

None of the following checks are present:
- `answeredInRound >= roundId` — detects an incomplete round
- `updatedAt != 0` — detects an unstarted round
- `block.timestamp - updatedAt <= MAX_STALENESS` — detects a price not refreshed within an acceptable window
- `price > 0` — detects a zero or negative answer (a negative `int256` cast to `uint256` in Solidity 0.8.x wraps to a huge value rather than reverting)

By contrast, the sister contract `ChainlinkOracleForRSETHPoolCollateral` at lines 30–32 performs the first three checks:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The stale price propagates through the following confirmed call chain:

1. `LRTOracle.getAssetPrice(asset)` delegates to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)`, resolving to `ChainlinkPriceOracle`.
2. `LRTDepositPool.getRsETHAmountToMint()` computes `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()` — the live (stale) asset price is used directly.
3. `LRTWithdrawalManager.getExpectedAssetAmount()` computes `underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset)` — again using the stale price.
4. `LRTOracle._getTotalEthInProtocol()` multiplies `getAssetPrice(asset)` by total deposits to compute protocol TVL, which feeds `_updateRsETHPrice()`.

The `pricePercentageLimit` guard in `_updateRsETHPrice()` only triggers when `updateRSETHPrice()` is explicitly called; it does not gate the per-deposit minting calculation at line 520 of `LRTDepositPool.sol`.

## Impact Explanation
**High — Theft of unclaimed yield.**

When a Chainlink LST/ETH feed goes stale at a price higher than the actual market price (e.g., stETH was 1.05 ETH but has since dropped to 0.99 ETH due to a slashing event, while the feed has not yet triggered a deviation update), an attacker deposits LST and receives rsETH computed at the inflated rate. The excess rsETH is backed by no real ETH value. When the oracle eventually corrects, the rsETH price drops proportionally, transferring yield from all existing holders to the attacker. This is a concrete, quantifiable theft of unclaimed yield from existing rsETH holders, matching the allowed High impact class.

## Likelihood Explanation
Chainlink LST/ETH feeds (e.g., stETH/ETH) have heartbeat intervals of up to 24 hours. During network congestion, oracle node failures, or feed deprecation, the feed can remain stale for the entire heartbeat window without any on-chain revert. No privileged access is required — `depositAsset()` is callable by any user. An attacker monitoring Chainlink feed timestamps can detect the staleness condition and act within the same block. The condition is passive and requires no oracle operator compromise.

## Recommendation
Apply the same pattern already used in `ChainlinkOracleForRSETHPoolCollateral`, extended with a maximum-age bound, inside `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();
```

`MAX_STALENESS` should be set per-feed based on the feed's documented heartbeat (e.g., 25 hours for a 24-hour heartbeat feed).

## Proof of Concept
1. The stETH/ETH Chainlink feed last updated at `T-20h` with price `1.05e18`. The actual stETH price has since dropped to `0.99e18` due to a slashing event, but the feed has not yet triggered a deviation update.
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, 0, "")`.
3. `getRsETHAmountToMint(stETH, 100e18)` computes `100e18 * 1.05e18 / rsETHPrice`. With `rsETHPrice = 1.04e18` (last correctly stored), the attacker receives `≈ 100.96 rsETH` instead of the correct `≈ 95.19 rsETH` (based on actual 0.99 ETH price).
4. The attacker holds `≈ 5.77` excess rsETH backed by no real ETH value, diluting all existing holders proportionally when the oracle corrects.
5. No admin action, no privileged role, and no oracle operator compromise is required.

**Foundry fork test plan:**
- Fork mainnet at a block where a stETH/ETH Chainlink feed answer is older than the heartbeat threshold.
- Deploy or point to the existing `ChainlinkPriceOracle` with the stale feed.
- Call `depositAsset(stETH, 100e18, 0, "")` as an unprivileged address.
- Assert that `rsethAmountToMint` exceeds the amount that would be minted using the correct current price, confirming the dilution.