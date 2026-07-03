Audit Report

## Title
Stale Chainlink Price Accepted Without Freshness Validation, Enabling Over-Minting of rsETH - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards `updatedAt` and `answeredInRound`, accepting any price regardless of age. A stale Chainlink feed returning an inflated LST/ETH price allows an unprivileged depositor to mint more rsETH than the deposited assets are worth, extracting value from existing rsETH holders. The same codebase already implements the correct staleness checks in the sibling `ChainlinkOracleForRSETHPoolCollateral` contract.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` at L49–55 fetches the price but silently drops all return values except `price`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52
(, int256 price,,,) = priceFeed.latestRoundData();
```

No `require(block.timestamp - updatedAt <= heartbeat)` and no `require(answeredInRound >= roundId)` are present.

The sibling contract `ChainlinkOracleForRSETHPoolCollateral.getRate()` at L27–32 demonstrates the correct pattern:

```solidity
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The stale price propagates through two paths:

**Path 1 — rsETH minting:** `LRTDepositPool.getRsETHAmountToMint()` at L519–520 computes `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`. `lrtOracle.getAssetPrice(asset)` delegates directly to `ChainlinkPriceOracle.getAssetPrice()`. If the Chainlink feed is stale and returns an inflated price, the numerator is inflated and the attacker receives excess rsETH.

**Path 2 — rsETH price update:** `LRTOracle._getTotalEthInProtocol()` at L336–343 calls `getAssetPrice(asset)` for every supported LST to compute total protocol TVL, which then sets `rsETHPrice`. A stale inflated price overstates TVL and inflates `rsETHPrice`.

The deposit entry point `LRTDepositPool.depositAsset()` at L99–118 carries no role restriction — it is callable by any address.

The `pricePercentageLimit` guard in `_updateRsETHPrice()` at L252–266 only fires when `updateRSETHPrice()` is explicitly called and only blocks price *increases* beyond the threshold; it does not protect individual deposit transactions from using a stale asset price.

## Impact Explanation
**Critical — Direct theft of user funds.**

An attacker acquires a depegged LST at its true (lower) market price, then deposits it at the stale (higher) oracle price. `getRsETHAmountToMint` mints rsETH proportional to the stale price, so the attacker receives rsETH worth more ETH than was deposited. Redeeming that rsETH extracts ETH-denominated value from existing rsETH holders, constituting direct theft of at-rest user funds. The magnitude scales with deposit size and the price gap between the stale feed and the true market price.

## Likelihood Explanation
**Medium.** Chainlink feeds have documented heartbeat intervals (e.g., 1 hour for stETH/ETH on mainnet). During low-volatility periods the feed may not update for the full heartbeat window; during network congestion or oracle downtime it can go stale for longer. An LST depeg event is precisely the scenario where (a) the real price drops and (b) the oracle may lag. The attack requires no special privileges, no victim interaction, and no governance action — only a stale feed and a willing depositor. This class of vulnerability has been exploited in production protocols.

## Recommendation
Add round-completeness and time-based staleness checks to `ChainlinkPriceOracle.getAssetPrice()`, consistent with the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > heartbeatByAsset[asset]) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

The heartbeat threshold should be stored per asset feed (via a `heartbeatByAsset` mapping set by `onlyLRTManager`) since different Chainlink feeds have different update frequencies.

## Proof of Concept
1. Deploy or fork mainnet with stETH/ETH Chainlink feed. Advance time past the feed's heartbeat without triggering a feed update (simulating a stale feed that still returns the last price of 1.0 ETH).
2. Acquire 1000 stETH at the true market price of 0.95 ETH each (total cost: 950 ETH).
3. Call `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")` as an unprivileged address.
4. `getRsETHAmountToMint` computes: `rsethAmountToMint = (1000e18 × 1.0e18) / rsETHPrice`. With `rsETHPrice = 1.0e18`, the attacker receives 1000 rsETH.
5. The correct amount (based on true price 0.95 ETH) would be `(1000e18 × 0.95e18) / 1.0e18 = 950 rsETH`.
6. The attacker holds 50 excess rsETH, each redeemable for ~1 ETH of protocol assets — a ~50 ETH extraction from existing rsETH holders.
7. Repeat with larger deposit sizes or across multiple supported LSTs for proportionally larger extraction.

Foundry fork test: set `vm.warp(block.timestamp + 2 hours)` after the last Chainlink round update, then call `depositAsset` and assert that `rsethAmountToMint` exceeds `(depositAmount * truePrice) / rsETHPrice`.