Audit Report

## Title
Unvalidated Chainlink `latestRoundData()` Response Enables Stale Price Consumption, Corrupting rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validity fields (`updatedAt`, `answeredInRound`, `answer` sign), accepting stale or invalid prices without reversion. A stale price silently propagates through `LRTOracle._updateRsETHPrice()` into the on-chain `rsETHPrice`, which governs all minting calculations. Because `updateRSETHPrice()` is a public, permissionless function, any caller can lock in a corrupted price during a staleness window, enabling theft of unclaimed yield from existing rsETH holders.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` (L49–55) reads only the raw `price` field:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No checks are performed on `updatedAt` (staleness), `answeredInRound >= roundId` (round completeness), or `price > 0` (sign validity). This is in direct contrast with the sibling contract `ChainlinkOracleForRSETHPoolCollateral.getRate()` (L26–37), which validates all three conditions before returning a price.

The corrupted price flows as follows:
1. `LRTOracle.updateRSETHPrice()` (L87–89) is `public` with no access control beyond `whenNotPaused`.
2. It calls `_updateRsETHPrice()` → `_getTotalEthInProtocol()` (L331–349), which iterates all supported assets and calls `getAssetPrice(asset)` for each.
3. `getAssetPrice` delegates to `ChainlinkPriceOracle.getAssetPrice()`, returning the unvalidated stale price.
4. The resulting `totalETHInProtocol` is used to compute `newRsETHPrice = (totalETHInProtocol - protocolFeeInETH) / rsethSupply` (L250), which is then stored as `rsETHPrice` (L313).
5. `LRTDepositPool.getRsETHAmountToMint()` (L506–521) uses `rsETHPrice` directly: `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`.

The downside protection in `_updateRsETHPrice()` (L270–282) only auto-pauses if `pricePercentageLimit > 0` AND the price drop exceeds the configured threshold. For small staleness deviations (e.g., stETH/ETH feed stale within the configured limit, or when `pricePercentageLimit == 0`), the corrupted price is accepted and stored without any reversion.

## Impact Explanation
**High — Theft of unclaimed yield.**

When a Chainlink feed goes stale at a price lower than the true market price (e.g., stETH/ETH stale at 0.990 while true rate is 0.999):
- `totalETHInProtocol` is understated by `stETH_balance × 0.009`.
- `rsETHPrice` is set below its true value.
- A new depositor calling `depositETH` or `depositAsset` receives `amount × 1e18 / rsETHPrice` rsETH — more rsETH than their deposit is actually worth at the true exchange rate.
- When the oracle recovers and `rsETHPrice` is corrected upward, the attacker's rsETH is worth more than deposited, at the direct expense of all prior rsETH holders whose accrued yield has been diluted.

This matches the allowed impact: **High — Theft of unclaimed yield.**

A secondary impact exists if a Chainlink feed returns `price <= 0`: `uint256(price)` wraps to near `type(uint256).max`, inflating `rsETHPrice` astronomically and causing `getRsETHAmountToMint` to return 0 for any normal deposit, reverting with `MinimumAmountToReceiveNotMet` — a **Medium — Temporary freezing of funds**. This is less likely in practice but is a direct consequence of the missing `price > 0` check.

## Likelihood Explanation
Chainlink feeds can go stale during Ethereum network congestion (oracle update transactions fail to land), Chainlink node outages, or during heartbeat gaps on low-volatility feeds (e.g., stETH/ETH has a 24-hour heartbeat, meaning a stale price can persist for up to 24 hours). No privileged access is required. Any external caller can invoke `updateRSETHPrice()` at the moment a feed is stale, locking in the corrupted price. The attack is repeatable across any staleness window and requires only a standard deposit transaction after triggering the price update.

## Recommendation
Add staleness and validity checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice(); // per-feed heartbeat

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_STALENESS` should be set per asset to match the Chainlink feed's documented heartbeat (e.g., 24 hours for stETH/ETH, 1 hour for ETHx/ETH).

## Proof of Concept
**Foundry fork test outline:**

1. Fork mainnet at a block where the stETH/ETH Chainlink feed is within its heartbeat.
2. Warp `block.timestamp` forward by 25 hours (past the 24-hour heartbeat) without advancing the feed's `updatedAt`.
3. Call `LRTOracle.updateRSETHPrice()` as an unprivileged EOA — succeeds, stores the stale (lower) price as `rsETHPrice`.
4. Deposit ETH via `LRTDepositPool.depositETH(minRSETH, "")` — receive more rsETH than the true rate entitles.
5. Warp back to present; call `updateRSETHPrice()` again with a live feed — `rsETHPrice` corrects upward.
6. Assert that the attacker's rsETH balance is worth more ETH than deposited, and that existing holders' share of `totalETHInProtocol` has decreased proportionally.

**Minimal call sequence (no fork):**
1. Deploy a mock `AggregatorV3Interface` returning a stale `updatedAt` (e.g., `block.timestamp - 25 hours`) and a price 1% below true.
2. Set it as the price feed for stETH in `ChainlinkPriceOracle`.
3. Call `LRTOracle.updateRSETHPrice()` — observe `rsETHPrice` set below true value with no revert.
4. Call `LRTDepositPool.getRsETHAmountToMint(ETH, 1 ether)` — observe inflated rsETH amount returned.