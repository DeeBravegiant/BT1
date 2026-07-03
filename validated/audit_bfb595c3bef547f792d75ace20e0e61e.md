Audit Report

## Title
Missing Staleness and Validity Checks on Chainlink `latestRoundData()` Return Values - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` discards all validation fields from `latestRoundData()`, allowing a stale or zero price to propagate into `LRTOracle._updateRsETHPrice()`. Because `updateRSETHPrice()` is a public, permissionless function, any caller can lock in a stale-price-derived rsETH exchange rate, causing either dilution of existing holders (deflated stale price) or shortchanging of new depositors (inflated stale price).

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the LST/ETH price with no validation:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

None of `roundId`, `updatedAt`, `answeredInRound`, or the sign of `price` are checked. The sibling contract `ChainlinkOracleForRSETHPoolCollateral.getRate()` applies all three standard guards (`answeredInRound < roundID`, `timestamp == 0`, `ethPrice <= 0`) on the identical interface, confirming the protocol is aware of the pattern and deliberately chose not to apply it here.

The unvalidated price flows through `LRTOracle.getAssetPrice()` → `_getTotalEthInProtocol()` → `_updateRsETHPrice()`, which writes the derived value to the `rsETHPrice` storage variable. `updateRSETHPrice()` is `public whenNotPaused` with no role restriction, so any EOA can trigger the full update path.

`_updateRsETHPrice()` does contain a `pricePercentageLimit` guard, but it is initialized to `0` (no assignment in `initialize()`), and both the upside and downside checks are gated on `pricePercentageLimit > 0`, meaning the guard is entirely inactive by default. Even when set, deviations within the configured threshold pass through unchecked.

Exploit path:
1. A Chainlink LST/ETH feed misses its heartbeat; `updatedAt` is now stale while the true price has moved.
2. Attacker observes the stale feed on-chain (no privileged access needed).
3. Attacker calls `LRTOracle.updateRSETHPrice()`.
4. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice()`, which returns the stale price without reverting.
5. `rsETHPrice` is written to storage with the stale-price-derived value.
6. Any subsequent `LRTDepositPool.depositAsset()` call mints rsETH at the incorrect price.
7. When the feed updates and `updateRSETHPrice()` is called again, the price corrects, but the over- or under-minted rsETH supply is permanent.

## Impact Explanation
**High — Theft of unclaimed yield.**

In the deflated-stale-price scenario, `_getTotalEthInProtocol()` understates TVL, `rsETHPrice` is set below its true value, and new depositors receive more rsETH than they are entitled to. This dilutes the pro-rata share of every existing rsETH holder, transferring accrued yield from existing holders to the new depositors — a concrete, irreversible theft of unclaimed yield. The damage is permanent because the over-minted rsETH cannot be recalled once the price corrects.

## Likelihood Explanation
**Low.** Chainlink feeds go stale during network congestion, sequencer downtime, or feed deprecation. The attacker requires no capital, no privileged role, and no ability to cause the staleness — only the ability to observe it and call a public function. The window per event is narrow (minutes to hours), but the attack is repeatable across every future staleness event and requires no setup beyond monitoring.

## Recommendation
Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
uint256 private constant STALENESS_THRESHOLD = 3600; // tune per feed heartbeat

function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (price <= 0) revert InvalidPrice();
    if (answeredInRound < roundId) revert StalePrice();
    if (block.timestamp > updatedAt + STALENESS_THRESHOLD) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, `RSETHPriceFeed.latestRoundData()` passes through all fields from `ETH_TO_USD.latestRoundData()` without any validation before composing the answer; equivalent checks should be applied there as well.

## Proof of Concept
Foundry fork test outline:

```solidity
// 1. Fork mainnet at a block where a supported LST/ETH feed is fresh.
// 2. Warp block.timestamp forward by > heartbeat interval (e.g., 2 hours)
//    without advancing the feed's updatedAt (simulating a missed heartbeat).
// 3. Record rsETHPrice before the attack.
// 4. Call LRTOracle.updateRSETHPrice() from an unprivileged address.
//    Assert: call succeeds (no revert), rsETHPrice is updated to stale-derived value.
// 5. Have a test depositor call LRTDepositPool.depositAsset() with a known amount.
//    Record rsethAmountMinted.
// 6. Warp back to real time, call updateRSETHPrice() again with fresh feed data.
//    Assert: rsETHPrice corrects upward (deflated-stale scenario).
// 7. Compute expected rsethAmountMinted at the corrected price.
//    Assert: actual minted > expected, quantifying the dilution to existing holders.
```

The test requires no privileged keys, no flash loans, and no oracle operator compromise — only a public function call during a naturally occurring staleness window.