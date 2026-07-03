Audit Report

## Title
Missing Staleness Validation in `ChainlinkPriceOracle.getAssetPrice()` Allows Stale Prices to Corrupt rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls Chainlink's `latestRoundData()` but silently discards all staleness-related return values (`roundId`, `updatedAt`, `answeredInRound`), performing no round-completeness or heartbeat check. A stale feed propagates an outdated asset price into `_updateRsETHPrice()`, which is publicly callable with no access control, causing deposits and withdrawals to execute at an incorrect exchange rate. The same codebase applies partial staleness checks in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, confirming awareness of the pattern.

## Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol` at L52, `getAssetPrice()` destructures only `answer` from `latestRoundData()`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

`roundId`, `startedAt`, `updatedAt`, and `answeredInRound` are all discarded. There is no check of the form `answeredInRound < roundId` (incomplete round) or `block.timestamp - updatedAt > heartbeat` (time-based staleness).

This price flows directly into `LRTOracle.getAssetPrice()` (L156–158), which is called by `_getTotalEthInProtocol()` (L339), which feeds `_updateRsETHPrice()` (L250). `updateRSETHPrice()` (L87–89) is `public` with only a `whenNotPaused` guard — any external caller can commit a stale price to state.

The `pricePercentageLimit` circuit breaker (L252–281) is not a sufficient mitigation: (a) it defaults to `0` (never initialized in `initialize()`), in which case the check `pricePercentageLimit > 0 && ...` is always false and provides zero protection; (b) even when set, a stale price deviation within the configured limit passes through unchecked.

By contrast, `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` (L30–32) applies `answeredInRound < roundID` and `timestamp == 0` checks, confirming the project is aware of the staleness pattern but did not apply it to the primary oracle.

## Impact Explanation
When a Chainlink LST/ETH feed goes stale, `rsETHPrice` is computed from an incorrect `totalETHInProtocol`. Depositors receive rsETH at a wrong rate (too many or too few tokens); withdrawers receive incorrect asset amounts. This constitutes the contract failing to deliver promised returns. In the deflated-price direction, new depositors receive excess rsETH, diluting existing holders' accrued yield — constituting theft of unclaimed yield from existing rsETH holders.

**Concrete allowed impact: Low — Contract fails to deliver promised returns (primary); High — Theft of unclaimed yield (in the deflated-price direction with concurrent deposits).**

## Likelihood Explanation
Chainlink feeds have historically gone stale during network stress, sequencer downtime, or oracle node outages. The affected oracle is the primary price source for all supported LST assets. `updateRSETHPrice()` is public and callable by any unprivileged address, so no attacker capability beyond a standard EOA is required. The stale price can be committed to state at any time during a feed outage without admin intervention.

**Likelihood: Low** — requires a Chainlink feed outage, which is uncommon but historically observed.

## Recommendation
Add both a round-completeness check and a time-based heartbeat check in `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > MAX_HEARTBEAT) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_HEARTBEAT` should be configurable per feed (e.g., 1 hour for ETH/USD, 24 hours for LST/ETH feeds on Ethereum mainnet).

## Proof of Concept

**Minimal fork test plan (Foundry):**

1. Fork Ethereum mainnet at a block where a supported LST/ETH Chainlink feed is live.
2. Warp `block.timestamp` forward by 48 hours (simulating a stale feed — `latestRoundData()` still returns the old `updatedAt`).
3. Call `LRTOracle.updateRSETHPrice()` from an unprivileged EOA.
4. Assert that `rsETHPrice` was updated using the stale price (no revert occurred).
5. Simulate a deposit at the stale rate and compare the rsETH minted against the correct rate computed with the actual current price.
6. Confirm the discrepancy constitutes incorrect token issuance.

**Call sequence:**
1. Chainlink LST/ETH feed goes stale (last `updatedAt` > heartbeat ago).
2. Attacker (any EOA) calls `LRTOracle.updateRSETHPrice()`.
3. `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `LRTOracle.getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice(asset)` → `latestRoundData()` returns stale price, no revert.
4. `rsETHPrice` is set to an incorrect value.
5. All subsequent deposits and withdrawals execute at the wrong exchange rate.