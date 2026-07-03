Audit Report

## Title
Missing Staleness and Validity Checks in `ChainlinkPriceOracle.getAssetPrice()` Causes Protocol-Wide Revert on Feed Failure - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` with no validation of the returned price or round data. If the Chainlink feed reverts or returns a zero/negative price, the revert propagates through every protocol function that depends on asset pricing — deposits, withdrawal initiations, and queue unlocking — temporarily freezing user funds. The same codebase already implements correct validation in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, confirming the omission is inconsistent.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` (L49–55) blindly casts the result of `latestRoundData()`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

There is no check on `updatedAt` (staleness), no `price > 0` guard, and no `answeredInRound >= roundId` check. By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` (L26–37) in the same repository validates all three conditions before using the price.

When `latestRoundData()` reverts (e.g., sequencer downtime, feed deprecation, price history gap), the revert propagates through the full call chain:

1. `LRTOracle.getAssetPrice(asset)` (L156–158) → delegates to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)`
2. `LRTOracle._getTotalEthInProtocol()` (L336–339) → calls `getAssetPrice(asset)` for every supported asset in a loop
3. `LRTDepositPool.getRsETHAmountToMint()` (L519–520) → calls `lrtOracle.getAssetPrice(asset)`, blocking all deposits
4. `LRTWithdrawalManager.getExpectedAssetAmount()` (L590–593) → calls `lrtOracle.getAssetPrice(asset)`, blocking `initiateWithdrawal()`
5. `LRTWithdrawalManager._createUnlockParams()` (L846–848) → calls `lrtOracle.getAssetPrice(asset)`, blocking `unlockQueue()`

Additionally, if `price` is zero or negative and the feed does not revert, `uint256(price)` silently underflows (for negative) or returns zero, corrupting the exchange rate used for minting and withdrawal calculations.

No fallback oracle exists. There is no `try/catch` around the oracle call. The protocol is entirely dependent on Chainlink feed liveness for all core operations.

## Impact Explanation
**Medium — Temporary freezing of funds.** When any supported asset's Chainlink feed becomes unavailable or reverts, all deposits, all new withdrawal initiations, and all `unlockQueue()` calls revert. Users who have already queued withdrawals cannot have their requests processed until the feed recovers. The freeze affects all users of the protocol, not just those interacting with the affected asset, because `_getTotalEthInProtocol()` iterates over all supported assets.

## Likelihood Explanation
Chainlink feeds are known to experience temporary outages: sequencer downtime on L2 networks, price history gaps during high volatility, and feed deprecation are documented failure modes. The protocol supports multiple LST assets (stETH, ETHx, rETH, sfrxETH, swETH), each with its own Chainlink feed — any single feed failure blocks the entire protocol. No privileged access or attacker action is required; the failure is triggered by normal user interactions (deposit, withdraw) during an oracle outage.

## Recommendation
1. Add staleness and validity checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral.getRate()`: validate `answeredInRound >= roundId`, `updatedAt != 0`, and `price > 0`.
2. Add a configurable staleness threshold (e.g., `block.timestamp - updatedAt <= maxStaleness`) appropriate for each asset's feed heartbeat.
3. Consider wrapping the `getAssetPrice()` call in `_getTotalEthInProtocol()` with a `try/catch` that falls back to a reserve oracle or the last known price, preventing a single feed failure from blocking all protocol operations.

## Proof of Concept
1. Chainlink feed for stETH/ETH becomes temporarily unavailable; `latestRoundData()` reverts.
2. User calls `LRTDepositPool.depositAsset(stETH, amount, minRSETH)`.
3. Call chain: `depositAsset` → `getRsETHAmountToMint` → `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → `priceFeed.latestRoundData()` reverts.
4. The deposit reverts. Simultaneously, any operator calling `unlockQueue(stETH, ...)` also reverts via `_createUnlockParams` → `lrtOracle.getAssetPrice(stETH)`.
5. All queued stETH withdrawals are frozen until the Chainlink feed recovers.

Foundry fork test plan:
```solidity
// Fork mainnet, mock the stETH/ETH Chainlink feed to revert on latestRoundData()
// vm.mockCallRevert(stEthFeed, abi.encodeWithSelector(AggregatorV3Interface.latestRoundData.selector), "feed down");
// Assert depositAsset reverts
// Assert initiateWithdrawal reverts
// Assert unlockQueue reverts
```