Audit Report

## Title
Missing `price > 0` Validation in `ChainlinkPriceOracle.getAssetPrice()` Allows Zero-Price Propagation - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and casts the raw `int256 price` to `uint256` without any positivity check. A zero price silently propagates into deposit minting, TVL computation, and withdrawal calculations. The same codebase's `ChainlinkOracleForRSETHPoolCollateral` already applies `if (ethPrice <= 0) revert InvalidPrice()`, confirming developer awareness of the pattern. The omission enables both direct depositor fund loss and a protocol-wide temporary freeze.

## Finding Description

**Root cause:** `ChainlinkPriceOracle.getAssetPrice()` performs no validation on the returned price:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L49-55
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

When `price == 0`, `uint256(0)` is returned with no revert. This zero flows into three critical paths:

**Path 1 — Deposit fund loss (`LRTDepositPool`):**
`getRsETHAmountToMint` computes `rsethAmountToMint = (amount * 0) / rsETHPrice = 0`. In `_beforeDeposit`, the slippage guard is `if (rsethAmountToMint < minRSETHAmountExpected)` — if the caller passes `minRSETHAmountExpected = 0`, this check passes. `safeTransferFrom` then pulls the user's tokens, and `_mintRsETH(0)` mints nothing. The user's deposited assets are taken with zero rsETH issued.

**Path 2 — Protocol-wide temporary freeze (`LRTOracle`):**
`_getTotalEthInProtocol()` calls `getAssetPrice(asset)` for each supported asset. A zero price for any asset zeroes out that asset's entire TVL contribution. The resulting artificially low `newRsETHPrice` can fall below `highestRsethPrice` by more than `pricePercentageLimit`, triggering the automatic pause of `lrtDepositPool`, `withdrawalManager`, and the oracle itself — freezing all deposits and withdrawals protocol-wide.

**Path 3 — Withdrawal revert (`LRTWithdrawalManager`):**
`getExpectedAssetAmount` computes `amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset)`. Division by zero reverts, freezing all withdrawal amount queries and execution for the affected asset.

**Existing guard omitted:** `ChainlinkOracleForRSETHPoolCollateral.getRate()` applies `if (ethPrice <= 0) revert InvalidPrice()` (line 32), confirming the developers know this check is necessary but did not apply it to the primary oracle.

## Impact Explanation

- **Critical — Direct theft of user funds:** A depositor calling `depositAsset(asset, amount, 0, "")` during a zero-price window has their full `amount` transferred in while receiving 0 rsETH. This is an irreversible loss of deposited principal at the time of the transaction.
- **Medium — Temporary freezing of funds:** A zero price for any supported asset causes `updateRSETHPrice()` (a public, permissionless function) to compute a sharply reduced `newRsETHPrice`, potentially crossing the `pricePercentageLimit` threshold and auto-pausing the deposit pool, withdrawal manager, and oracle simultaneously, freezing all user funds until an admin manually unpauses.

## Likelihood Explanation

Chainlink feeds can return `price == 0` during circuit-breaker activations, feed deprecation, or the initialization window of a newly deployed aggregator. `updatePriceFeedFor()` requires only `LRTManager` role and a non-zero address — no price sanity check is performed at registration time. The public `updateRSETHPrice()` function means any external caller can trigger the freeze path the moment a zero price is live. The deposit loss path requires the depositor to pass `minRSETHAmountExpected = 0`, which is a common pattern used by aggregators, smart contract integrators, and users who omit slippage protection.

## Recommendation

Add a positivity check in `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    if (price <= 0) revert InvalidPrice();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, add staleness checks (`answeredInRound < roundId`, `updatedAt == 0`) consistent with `ChainlinkOracleForRSETHPoolCollateral`.

## Proof of Concept

1. Chainlink feed for a supported LST (e.g., stETH) enters a circuit-breaker state; `latestRoundData()` returns `price = 0`.
2. Attacker/user calls `LRTDepositPool.depositAsset(stETH, 1e18, 0, "")`.
3. `_beforeDeposit` → `getRsETHAmountToMint(stETH, 1e18)` → `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns `0`.
4. `rsethAmountToMint = (1e18 * 0) / rsETHPrice = 0`.
5. `0 >= 0` (minRSETHAmountExpected) passes the slippage check.
6. `safeTransferFrom(user, depositPool, 1e18)` executes — user's 1 stETH is taken.
7. `_mintRsETH(0)` — user receives 0 rsETH.
8. Separately, any caller invokes `updateRSETHPrice()`; `_getTotalEthInProtocol()` computes stETH TVL contribution as 0, `newRsETHPrice` drops sharply, `isPriceDecreaseOffLimit` triggers, and `lrtDepositPool.pause()` + `withdrawalManager.pause()` + `_pause()` execute — all user funds are frozen until admin intervention.

**Foundry fork test outline:**
```solidity
// Fork mainnet, mock Chainlink stETH feed to return price=0
// Call depositAsset(stETH, 1e18, 0, "")
// Assert: stETH balance of depositPool increased by 1e18
// Assert: rsETH balance of caller == 0
// Call updateRSETHPrice()
// Assert: lrtDepositPool.paused() == true
```