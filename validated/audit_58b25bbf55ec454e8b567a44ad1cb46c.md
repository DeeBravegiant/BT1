Audit Report

## Title
Stale Chainlink Price Data Accepted Without Staleness Validation in `ChainlinkPriceOracle`, Enabling Incorrect rsETH Price Computation - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards the `updatedAt` timestamp and `answeredInRound` fields, accepting arbitrarily stale prices without reversion. This stale price flows directly into `LRTOracle._getTotalEthInProtocol()` → `_updateRsETHPrice()`, causing `rsETHPrice` to be set incorrectly. Because `updateRSETHPrice()` is a public, permissionless function, any external caller can lock in a stale deflated price and immediately deposit to receive excess rsETH, diluting all existing rsETH holders.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` at line 52 reads only the `price` field:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The `updatedAt` and `answeredInRound` return values are silently discarded. No check of the form `require(updatedAt + heartbeat > block.timestamp)` or `require(answeredInRound >= roundId)` exists.

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repository performs both checks at lines 30–31:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
```

The stale price returned by `ChainlinkPriceOracle` is consumed by `LRTOracle._getTotalEthInProtocol()` at line 339 (`uint256 assetER = getAssetPrice(asset)`), which accumulates `totalETHInProtocol`. This value feeds directly into `_updateRsETHPrice()` at line 250:

```solidity
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

`updateRSETHPrice()` at line 87 is `public whenNotPaused` — no role restriction — so any external caller can trigger it at any time.

**Exploit path:**
1. A Chainlink LST/ETH feed (e.g., stETH/ETH) goes stale; the last reported price is below the current market price (e.g., 5% lower, within the `pricePercentageLimit` threshold so no pause is triggered).
2. Attacker calls the public `LRTOracle.updateRSETHPrice()`.
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)`, which returns the stale deflated price without reverting.
4. `totalETHInProtocol` is underestimated; `newRsETHPrice` is set ~5% below the true value and stored in `rsETHPrice`.
5. Attacker immediately calls `LRTDepositPool.depositAsset(stETH, largeAmount)`, receiving ~5% more rsETH than the true exchange rate entitles them to.
6. When the Chainlink feed resumes and `rsETHPrice` is corrected upward, the attacker's excess rsETH represents a direct dilution of all pre-existing rsETH holders' share of the protocol TVL.

Existing guards are insufficient: the `pricePercentageLimit` downside protection at lines 270–281 only triggers a pause if the price drop exceeds the configured threshold; a staleness event within the threshold window bypasses it entirely.

## Impact Explanation
**High — Theft of unclaimed yield.** Existing rsETH holders accumulate yield as LST prices appreciate. When an attacker exploits a stale deflated price to receive excess rsETH at a discounted rate, the attacker's inflated rsETH share dilutes the proportional claim of all pre-existing holders on the protocol's TVL. When the price corrects, the attacker's excess rsETH represents yield extracted from existing holders without their consent. This is a concrete, quantifiable transfer of value from existing holders to the attacker.

A secondary impact is **Medium — Temporary freezing of funds**: if the stale price drop exceeds `pricePercentageLimit`, the downside protection at lines 277–281 pauses `lrtDepositPool`, `withdrawalManager`, and `LRTOracle`, freezing all user deposits and withdrawals until an admin unpauses.

## Likelihood Explanation
Chainlink feeds have documented heartbeat intervals (e.g., 24 hours for some LST/ETH feeds). During low-volatility periods, feeds routinely go the full heartbeat without updating. Network congestion or oracle operator delays can extend this further. No privileged access is required: `updateRSETHPrice()` is callable by any EOA or contract. The attacker need only monitor the Chainlink feed's `updatedAt` timestamp off-chain and call `updateRSETHPrice()` when staleness is detected. This is a realistic, repeatable, externally reachable condition.

## Recommendation
Add staleness and round-completeness checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();
require(answeredInRound >= roundId, "Stale price");
require(updatedAt != 0, "Incomplete round");
require(block.timestamp - updatedAt <= MAX_STALENESS, "Price too old");
require(price > 0, "Invalid price");
```

Introduce a per-asset configurable `MAX_STALENESS` parameter to accommodate different Chainlink feed heartbeat intervals (e.g., 1 hour for ETH/USD, 24 hours for some LST/ETH feeds).

## Proof of Concept
**Foundry fork test outline:**
1. Fork mainnet at a block where the stETH/ETH Chainlink feed has not updated for >1 hour (or use `vm.warp` to advance time past the last `updatedAt`).
2. Deploy/use the existing `ChainlinkPriceOracle` with the stETH feed registered.
3. Record `rsETHPrice` before the attack.
4. Call `LRTOracle.updateRSETHPrice()` as an unprivileged address.
5. Assert that `rsETHPrice` was updated to a value below the true market rate (verifiable against a reference oracle or the feed's actual last price vs. current market).
6. Call `LRTDepositPool.depositAsset(stETH, largeAmount)` as the attacker.
7. Record the rsETH minted to the attacker.
8. Advance time, allow the Chainlink feed to update (or mock an updated price), call `updateRSETHPrice()` again.
9. Assert that the attacker's rsETH balance now represents a larger share of TVL than their deposit entitled them to, at the expense of pre-existing holders.