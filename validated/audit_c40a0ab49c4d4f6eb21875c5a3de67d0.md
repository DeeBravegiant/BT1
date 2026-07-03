Audit Report

## Title
Stale Chainlink Price Data Accepted Without Staleness Validation in `ChainlinkPriceOracle` - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` discards the `updatedAt` and `answeredInRound` fields from `latestRoundData()`, accepting arbitrarily stale prices. However, the claimed theft-of-yield impact via the deposit path is not achievable as described, because the deposit minting formula uses the same stale oracle price in both numerator and denominator, causing the staleness to cancel out. The valid residual impact is a stale-price-triggered false-positive downside-protection pause, constituting temporary freezing of funds.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice()` reads only `price` from `latestRoundData()`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol line 52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No `updatedAt` heartbeat check or `answeredInRound >= roundId` check is performed, unlike `ChainlinkOracleForRSETHPoolCollateral.getRate()` which performs both.

`updateRSETHPrice()` is public and permissionless. Any caller can invoke it while a feed is stale, causing `rsETHPrice` to be set to a deflated value via `_updateRsETHPrice()` → `_getTotalEthInProtocol()`.

**Why the theft-of-yield PoC fails:**

The deposit minting formula in `LRTDepositPool.getRsETHAmountToMint()` is:

```solidity
// contracts/LRTDepositPool.sol line 520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

Both `lrtOracle.getAssetPrice(asset)` (numerator) and `lrtOracle.rsETHPrice()` (denominator) are affected by the same stale price. When the attacker deposits immediately after calling `updateRSETHPrice()` while the oracle is still stale, the stale price P′ appears in both terms. In the single-asset case they cancel exactly. In the multi-asset case, let f = fraction of TVL in the stale asset and δ = staleness discount:

```
rsethMinted_stale / rsethMinted_correct = (1 − δ) / (1 − δf)
```

Since 0 < f < 1, this ratio is **less than 1**: the depositor receives *fewer* rsETH than correct, not more. The described Scenario A theft does not occur via the PoC's immediate-deposit path.

**The valid impact — false-positive pause:**

When `updateRSETHPrice()` is called with a stale (deflated) price, `newRsETHPrice` may fall below `highestRsethPrice` by more than `pricePercentageLimit`. The downside-protection logic at lines 270–281 of `LRTOracle.sol` then calls `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()`, freezing all deposits and withdrawals until an admin manually unpauses. This is reachable by any unprivileged caller via the public `updateRSETHPrice()`.

## Impact Explanation

**Medium — Temporary freezing of funds.**

Any external caller can invoke `updateRSETHPrice()` during a period of Chainlink feed staleness. If the stale price is sufficiently below `highestRsethPrice` (exceeding `pricePercentageLimit`), the protocol auto-pauses, freezing deposits and withdrawals for all users until an admin intervenes. This matches the allowed impact class "Temporary freezing of funds."

The claimed High impact (theft of unclaimed yield) is not valid: the deposit formula's use of the same stale oracle price in numerator and denominator prevents the attacker from extracting excess rsETH via the described immediate-deposit path.

## Likelihood Explanation

Chainlink LST/ETH feeds have documented heartbeat intervals (up to 24 hours). During low-volatility periods, feeds may not update for the full heartbeat window. Network congestion or oracle operator issues can extend staleness further. Since `updateRSETHPrice()` requires no privileges, any actor can trigger the pause at any time the feed is stale and the price deviation exceeds `pricePercentageLimit`. This is a realistic, externally reachable condition.

## Recommendation

Add staleness and round-completeness checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();
require(answeredInRound >= roundId, "Stale price");
require(updatedAt != 0, "Incomplete round");
require(block.timestamp - updatedAt <= MAX_STALENESS, "Price too old");
require(price > 0, "Invalid price");
```

Introduce a per-asset configurable `MAX_STALENESS` parameter to accommodate different Chainlink feed heartbeat intervals.

## Proof of Concept

1. `pricePercentageLimit` is set to a non-zero value (e.g., 1e16 = 1%).
2. Chainlink stETH/ETH feed goes stale; last reported price is >1% below `highestRsethPrice`-implied value.
3. Any external caller invokes `LRTOracle.updateRSETHPrice()` (public, no access control).
4. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale deflated price.
5. `newRsETHPrice` is computed below `highestRsethPrice` by more than `pricePercentageLimit`.
6. Lines 277–281 of `LRTOracle.sol` execute: `lrtDepositPool.pause()`, `withdrawalManager.pause()`, `_pause()`.
7. All user deposits and withdrawals are frozen until an admin with `LRTAdmin` role manually unpauses each contract.

**Foundry fork test outline:**
- Fork mainnet; mock Chainlink stETH/ETH feed to return a timestamp of `block.timestamp - 25 hours`.
- Set `pricePercentageLimit` to 1e16.
- Call `lrtOracle.updateRSETHPrice()` from an unprivileged EOA.
- Assert `lrtDepositPool.paused() == true` and `withdrawalManager.paused() == true`.
- Assert that a subsequent `depositAsset()` call reverts with `Pausable: paused`.