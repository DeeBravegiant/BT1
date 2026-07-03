Audit Report

## Title
Unsafe `int256` → `uint256` Cast Without Positivity Check Causes Deposit and Withdrawal DoS - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` casts the `int256 price` returned by Chainlink's `latestRoundData()` directly to `uint256` with no `price > 0` guard. In Solidity 0.8.x, explicit casts are always silent; a negative price silently becomes `type(uint256).max`, and the subsequent multiplication overflows and reverts. This propagates through `LRTOracle` into every deposit, withdrawal, and price-update path that depends on the affected asset's price feed.

## Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol` lines 52–54:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

There is no check that `price > 0` before the cast. In Solidity 0.8.x, explicit casts bypass the checked-arithmetic rules: `uint256(-1)` silently produces `type(uint256).max` (no revert at the cast site). The immediately following `* 1e18` then triggers a checked-arithmetic overflow revert.

The revert propagates through the following call chains:

- `LRTOracle.getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice(asset)` (line 157 of `LRTOracle.sol`)
- `LRTDepositPool.getRsETHAmountToMint()` → `lrtOracle.getAssetPrice(asset)` (line 520 of `LRTDepositPool.sol`) → called by `depositETH` and `depositAsset`
- `LRTOracle._getTotalEthInProtocol()` → `getAssetPrice(asset)` (line 339 of `LRTOracle.sol`) → called by `updateRSETHPrice()`
- `LRTWithdrawalManager.getExpectedAssetAmount()` → `lrtOracle.getAssetPrice(asset)` (line 593 of `LRTWithdrawalManager.sol`)

No existing guard in `LRTOracle` catches this at call time. The `updatePriceOracleForValidated` sanity check (lines 103–106 of `LRTOracle.sol`) runs only at oracle registration, not on every price fetch, so it provides no runtime protection.

## Impact Explanation
Any asset whose Chainlink feed returns a non-positive answer causes `getAssetPrice()` to revert, freezing all deposits (`depositETH`, `depositAsset`), all rsETH price updates (`updateRSETHPrice`), and all withdrawal amount calculations (`getExpectedAssetAmount`) for that asset. This constitutes **temporary freezing of funds** (Medium severity), matching the allowed impact scope.

## Likelihood Explanation
No attacker action is required. Chainlink aggregators enforce `minAnswer`/`maxAnswer` circuit-breaker bounds; when the real market price falls below `minAnswer`, the feed returns `minAnswer` (which can be `0` or even negative for some legacy feeds). Feed deprecation and migration periods can also produce zero answers. Any unprivileged user calling `depositETH`, `depositAsset`, or triggering `updateRSETHPrice` activates the revert path. The condition is externally triggered and repeatable for as long as the feed returns a non-positive value.

## Recommendation
Add an explicit positivity check immediately after the `latestRoundData()` call:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= 0) revert InvalidPrice();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Alternatively, use OpenZeppelin's `SafeCast.toUint256(int256 value)`, which reverts on negative input, making the failure explicit and auditable.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Minimal Foundry fork test sketch
// 1. Deploy a mock AggregatorV3Interface that returns price = -1
// 2. Register it via ChainlinkPriceOracle.updatePriceFeedFor(asset, mockFeed)
// 3. Call ChainlinkPriceOracle.getAssetPrice(asset)
//    → uint256(-1) = type(uint256).max (silent cast)
//    → type(uint256).max * 1e18 overflows → REVERT
// 4. Call LRTDepositPool.depositAsset(asset, amount, 0)
//    → internally calls getRsETHAmountToMint → getAssetPrice → REVERT
//    → deposit is frozen for the asset

int256 price = -1;
uint256 castedPrice = uint256(price);          // == type(uint256).max, no revert
uint256 result = castedPrice * 1e18;           // overflow → REVERT (Solidity 0.8.x)
```

The cast on line 54 of `ChainlinkPriceOracle.sol` is the necessary vulnerable step; the downstream multiplication in the same expression then causes the revert that freezes the deposit and withdrawal paths.