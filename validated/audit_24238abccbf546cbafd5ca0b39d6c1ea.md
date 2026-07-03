Audit Report

## Title
`ChainlinkPriceOracle.getAssetPrice()` Missing Staleness Validation Enables rsETH Share-Price Manipulation — (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards `roundId`, `updatedAt`, and `answeredInRound`, returning a potentially stale or zero price with no freshness check. This stale price propagates into `LRTOracle._updateRsETHPrice()`, corrupting the stored `rsETHPrice`. An unprivileged attacker can exploit the resulting mispriced `rsETHPrice` to receive excess rsETH on deposit, diluting existing holders' unclaimed yield.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` (L49–55) discards all validation fields from `latestRoundData()`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The sibling contract `ChainlinkOracleForRSETHPoolCollateral.getRate()` (L26–37) in the same repository explicitly guards against the same failure modes:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The stale price flows through the following confirmed call chain:

1. `LRTOracle._getTotalEthInProtocol()` (L339) calls `getAssetPrice(asset)` for every supported LST, accumulating `totalETHInProtocol`.
2. `LRTOracle._updateRsETHPrice()` (L250) computes `newRsETHPrice = (totalETHInProtocol − protocolFeeInETH).divWad(rsethSupply)` and writes it to `rsETHPrice` (L313).
3. `LRTDepositPool.getRsETHAmountToMint()` (L520) uses `rsETHPrice` as the denominator: `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`.
4. `LRTWithdrawalManager.getExpectedAssetAmount()` (L593) uses `rsETHPrice` as the numerator: `underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset)`.

The `pricePercentageLimit` guard in `_updateRsETHPrice()` (L256–266) only triggers on deviations exceeding the configured threshold; moderate staleness (e.g., 0.3–0.5% drift) passes silently and is written to storage.

## Impact Explanation
When a Chainlink LST feed becomes stale with a price below the true value and within the `pricePercentageLimit` threshold, `rsETHPrice` is set below its true value. An attacker depositing immediately after `updateRSETHPrice()` receives `rsethAmountToMint = ethAmount / rsETHPrice` — more rsETH than their ETH is worth at the true price. When the feed updates and `rsETHPrice` is corrected upward, the attacker's rsETH is worth more than deposited, diluting all existing rsETH holders' accumulated yield. This constitutes **theft of unclaimed yield** (High impact).

## Likelihood Explanation
`updateRSETHPrice()` is public and permissionless — any address can call it. Chainlink feeds can become stale during L2 sequencer downtime, network congestion, or oracle node failures. The attacker does not need to cause the staleness; they only need to observe it and call `updateRSETHPrice()` followed by `depositETH()` or `depositAsset()`. The inconsistency with `ChainlinkOracleForRSETHPoolCollateral` — which performs identical staleness checks — confirms this is an unintentional omission in the mainnet oracle path, not a deliberate design choice.

## Recommendation
Apply the same staleness and validity guards already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();
require(answeredInRound >= roundId, "Stale price");
require(updatedAt != 0, "Incomplete round");
require(price > 0, "Invalid price");
// Optionally: require(block.timestamp - updatedAt <= HEARTBEAT, "Price too old");
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

## Proof of Concept
1. A Chainlink LST feed (e.g., stETH/ETH) becomes stale, reporting a price 0.4% below true value — within the `pricePercentageLimit` threshold.
2. Attacker calls `LRTOracle.updateRSETHPrice()` (public, no access control).
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale low price; no revert.
4. `totalETHInProtocol` is underestimated → `newRsETHPrice` is ~0.4% below true value → stored as `rsETHPrice`.
5. Attacker calls `LRTDepositPool.depositETH{value: 100 ether}(0, "")`.
6. `getRsETHAmountToMint` computes `rsethAmountToMint = 100e18 * 1e18 / rsETHPrice` — attacker receives ~0.4% more rsETH than entitled.
7. Chainlink feed updates; next `updateRSETHPrice()` corrects `rsETHPrice` upward.
8. Attacker's rsETH is now worth more than deposited; existing holders' share value is diluted by the excess minted rsETH.

**Foundry fork test plan:** Fork mainnet, mock a stale Chainlink response for stETH/ETH (set `answeredInRound < roundId` and `updatedAt` to a past timestamp), call `updateRSETHPrice()`, record `rsETHPrice`, deposit 100 ETH as attacker, advance block to allow feed update, call `updateRSETHPrice()` again, assert attacker's rsETH value exceeds 100 ETH at the corrected price.