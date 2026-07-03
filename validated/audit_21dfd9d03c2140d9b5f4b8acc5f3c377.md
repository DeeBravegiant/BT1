Audit Report

## Title
Missing Chainlink `latestRoundData()` Return Value Validation Enables Stale-Price-Triggered Protocol Pause — (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards `updatedAt`, `answeredInRound`, and does not check that `price > 0`. Because `LRTOracle.updateRSETHPrice()` is a public, permissionless function, any caller can invoke it while a Chainlink feed is stale, causing the downside-protection logic in `_updateRsETHPrice()` to pause `LRTDepositPool` and `LRTWithdrawalManager`, temporarily freezing all user deposits and withdrawals.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the LST/ETH exchange rate with no validation:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

`roundId`, `updatedAt`, and `answeredInRound` are all discarded. The sister contract in the same repository already implements the correct pattern:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L27-32
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The exploit path:

1. A Chainlink LST/ETH feed goes stale (e.g., during L2 sequencer downtime or a 24-hour heartbeat miss). `updatedAt` lags, `answeredInRound < roundId`, and the last reported price is below the true market rate.
2. Any unprivileged user calls `LRTOracle.updateRSETHPrice()`, which is `public whenNotPaused` with no access control.
3. `_updateRsETHPrice()` calls `_getTotalEthInProtocol()`, which iterates over all supported assets and calls `getAssetPrice(asset)` for each, routing through `ChainlinkPriceOracle.getAssetPrice()`.
4. The stale, unvalidated price is accepted and used to compute `totalETHInProtocol`.
5. `newRsETHPrice` is computed as `(totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply)`.
6. If the stale price is sufficiently below the true rate, `newRsETHPrice < highestRsethPrice` and `diff > pricePercentageLimit.mulWad(highestRsethPrice)`, triggering the pause branch at lines 277–281, which calls `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()`.

Existing guards are insufficient: `pricePercentageLimit` is a downside-protection guard, not a staleness guard — it is precisely what gets triggered by the stale price. The `whenNotPaused` modifier on `updateRSETHPrice()` only prevents calls after the pause, not before.

## Impact Explanation

**Temporary freezing of funds — Medium.**

Once the pause is triggered, all user deposits (`LRTDepositPool.depositAsset`, `depositETH`) and all withdrawals (`LRTWithdrawalManager`) are frozen until an admin manually unpauses. No attacker funds are required; the trigger is a public function call during a naturally occurring feed staleness window. Users cannot access their deposited LSTs or pending withdrawals for the duration of the freeze.

A secondary Low impact exists: if the stale price is below the true rate but within `pricePercentageLimit`, `rsETHPrice` is set too low, and all subsequent withdrawals pay out less ETH than users are owed (contract fails to deliver promised returns).

## Likelihood Explanation

Chainlink LST/ETH feeds on Ethereum mainnet and L2s have documented 24-hour heartbeat intervals. During L2 sequencer downtime, periods of extreme gas congestion, or a feed deprecation event, `updatedAt` can lag well beyond the heartbeat with no on-chain signal. No attacker capability is required: any user who calls the public `updateRSETHPrice()` during such a window triggers the impact. The condition is repeatable whenever a feed enters a stale state.

## Recommendation

Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();
    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, add a per-feed configurable `maxStaleness` parameter and check `block.timestamp - updatedAt <= maxStaleness` to enforce a time-based freshness window.

## Proof of Concept

Foundry fork test outline:

1. Fork mainnet at a block where a stETH/ETH Chainlink feed is live.
2. Deploy or mock `ChainlinkPriceOracle` pointing to the real feed address.
3. Use `vm.mockCall` to make `latestRoundData()` return `answeredInRound = roundId - 1` and a price 5% below the current true rate (simulating a stale round).
4. Call `LRTOracle.updateRSETHPrice()` from an unprivileged EOA (`vm.prank(address(0xdead))`).
5. Assert `LRTDepositPool.paused() == true` and `LRTWithdrawalManager.paused() == true`.
6. Attempt `depositAsset(stETH, amount, 0, "")` from a user — assert it reverts with `Pausable: paused`.

This sequence requires no privileged access, no attacker funds, and no governance action.