Audit Report

## Title
Missing Chainlink Price Feed Staleness Validation Allows Stale Prices to Corrupt rsETH Rate Computation - (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards `updatedAt`, `answeredInRound`, and `roundId`, performing no staleness or validity checks. A stale Chainlink feed silently returns an outdated price that propagates into `LRTOracle._updateRsETHPrice()`, enabling either phantom fee minting (theft of unclaimed yield) or a false price-drop pause (temporary fund freeze), both triggerable by any public caller.

## Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol` at lines 52–54, `getAssetPrice()` fetches only the `price` field:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No checks are performed on `updatedAt` (staleness window), `answeredInRound >= roundId` (round completeness), or `price > 0` (non-negative). This price is consumed by `LRTOracle.getAssetPrice()` (line 157), which is called inside `_getTotalEthInProtocol()` (line 339), which feeds `_updateRsETHPrice()` (line 231).

`updateRSETHPrice()` at line 87 is `public` with no access control beyond `whenNotPaused`, so any external caller can trigger the stale-price path.

**Scenario A — Theft of unclaimed yield (High):** If the stale price is higher than the true current price (i.e., the LST has depreciated since the last Chainlink update), `totalETHInProtocol` is overstated. The condition at line 244 (`totalETHInProtocol > previousTVL`) is falsely satisfied, and protocol fees are minted as rsETH to the treasury at lines 299–307, diluting existing rsETH holders on phantom gains. The daily fee mint cap (`maxFeeMintAmountPerDay`) limits per-call damage but does not prevent the theft; the attack is repeatable each day.

**Scenario B — Temporary freezing of funds (Medium):** If the stale price is lower than the true current price, `newRsETHPrice` is understated. If the deviation exceeds `pricePercentageLimit`, the downside-protection logic at lines 270–281 executes `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()`, freezing all user deposits and withdrawals without any actual loss event.

Existing guards are insufficient: the `pricePercentageLimit` check at line 273 only acts as a trigger for the pause — it does not prevent a stale price from entering the computation. The `updatePriceOracleForValidated` sanity check (lines 103–106) only validates price range at oracle registration time, not at runtime.

## Impact Explanation
**High — Theft of unclaimed yield.** A stale-high LST price causes `totalETHInProtocol > previousTVL` to be falsely satisfied, minting rsETH fees to the treasury from phantom yield. This directly dilutes existing rsETH holders' share of real protocol assets. The impact is bounded per day by `maxFeeMintAmountPerDay` but is repeatable and requires no special privileges.

**Medium — Temporary freezing of funds** (secondary scenario). A stale-low price can trigger the automatic pause, freezing all deposits and withdrawals until an LRTAdmin manually unpauses.

## Likelihood Explanation
Chainlink heartbeat intervals (e.g., 1 hour for ETH/stETH) mean feeds can legally go without an update for the full heartbeat window during low volatility. `updateRSETHPrice()` is public and callable by any address with no access control. No special attacker capability is required — a normal EOA can call the function at any time. The stale-price condition arises from normal Chainlink operational behavior, not oracle compromise.

## Recommendation
Add staleness and validity checks in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
uint256 public constant MAX_STALENESS_DELAY = 3600; // set per-feed heartbeat

function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    require(price > 0, "Invalid price");
    require(answeredInRound >= roundId, "Stale round");
    require(block.timestamp - updatedAt <= MAX_STALENESS_DELAY, "Stale price");

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_STALENESS_DELAY` should be configurable per asset feed to match each Chainlink heartbeat plus a small buffer.

## Proof of Concept
**Scenario A (fee theft):**
1. Chainlink stETH/ETH feed last updated at price `P_stale > P_true` (e.g., stETH has since slightly depreciated).
2. Any EOA calls `LRTOracle.updateRSETHPrice()` (public, no access control).
3. `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `P_stale` without reverting.
4. `totalETHInProtocol` is overstated; `totalETHInProtocol > previousTVL` is satisfied.
5. `protocolFeeInETH` is computed on phantom gains; rsETH is minted to treasury, diluting holders.

**Scenario B (temporary freeze):**
1. Chainlink stETH/ETH feed goes stale at price `P_stale < P_true` (e.g., during network congestion).
2. Any EOA calls `LRTOracle.updateRSETHPrice()`.
3. `newRsETHPrice` is understated; `diff > pricePercentageLimit.mulWad(highestRsethPrice)` at line 273.
4. `lrtDepositPool.pause()`, `withdrawalManager.pause()`, `_pause()` execute — all user funds are frozen.

**Foundry fork test plan:** Fork mainnet, mock `latestRoundData()` to return a stale `updatedAt` (e.g., `block.timestamp - 7200`) with a manipulated price, call `updateRSETHPrice()` from an unprivileged address, and assert either (A) rsETH was minted to treasury with `totalETHInProtocol` overstated, or (B) `lrtDepositPool.paused() == true` with no real price drop.