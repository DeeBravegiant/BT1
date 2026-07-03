Audit Report

## Title
Missing Chainlink Staleness Check Enables Over-Redemption via `instantWithdrawal` — (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice` discards the `updatedAt` and `answeredInRound` return values from `latestRoundData`, performing no staleness check. `instantWithdrawal` consumes this potentially stale price as the denominator in `getExpectedAssetAmount` with no price-bounds guard, unlike `unlockQueue` which enforces caller-supplied min/max bounds via `_validatePrices`. When the Chainlink feed is stale and returns a deflated asset price, an attacker can burn a small amount of rsETH and redeem a disproportionately large amount of yield-bearing assets from `LRTUnstakingVault`.

## Finding Description

**Root cause 1 — no staleness check in `ChainlinkPriceOracle.getAssetPrice`:**

`contracts/oracles/ChainlinkPriceOracle.sol` line 52 discards all return values except `answer`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
```

`updatedAt` and `answeredInRound` are silently dropped. There is no check of the form `require(block.timestamp - updatedAt <= STALENESS_THRESHOLD)` or `require(answeredInRound >= roundId)`.

**Root cause 2 — `instantWithdrawal` has no price-bounds guard:**

`contracts/LRTWithdrawalManager.sol` line 228 calls `getExpectedAssetAmount` directly and uses the result without any sanity bound:

```solidity
uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
```

The formula at line 593 is:

```solidity
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

`rsETHPrice` is a stored value updated by `updateRSETHPrice()`. `getAssetPrice(asset)` is read live from Chainlink at call time. A stale, deflated asset price inflates `assetAmountUnlocked`.

**Contrast with `unlockQueue`:**

`unlockQueue` (lines 268–295) passes caller-supplied `minimumAssetPrice`/`maximumAssetPrice` to `_validatePrices` (lines 853–870), which reverts if the live price is outside the window. `instantWithdrawal` has no equivalent protection.

**Exploit flow:**

1. Chainlink feed for a supported LST (stETH, ETHx) goes stale — `updatedAt` is old, but the last reported price is below the current fair price.
2. Attacker calls `instantWithdrawal(asset, rsETHUnstaked, "")` — no staleness revert, no price-bounds revert.
3. `getExpectedAssetAmount` computes `rsETHUnstaked * rsETHPrice / staleDeflatedAssetPrice`, yielding a value larger than the fair ETH equivalent of the rsETH burned.
4. `unstakingVault.redeem(asset, assetAmountUnlocked)` transfers the inflated amount to the contract, and the attacker receives `assetAmountUnlocked - fee`.
5. The surplus drains yield-bearing assets from `LRTUnstakingVault` that belong to other protocol participants.

The only guard in `instantWithdrawal` is `CantInstantWithdrawMoreThanAvailable` (line 231–233), which limits the single-transaction drain but does not prevent the over-redemption per unit of rsETH burned.

## Impact Explanation

**Impact: High — Theft of unclaimed yield from `LRTUnstakingVault`.**

The attacker burns rsETH worth `X` ETH at the fair price but redeems assets worth `X * (fairPrice / stalePrice)` ETH. The surplus comes directly from `LRTUnstakingVault`, draining yield-bearing assets (stETH, ETHx, etc.) that belong to other protocol participants. The `instantWithdrawalFee` (0–10%) reduces but does not eliminate the profit when the price deviation is large enough. This matches the allowed impact class "Theft of unclaimed yield" (High).

## Likelihood Explanation

**Likelihood: Medium.**

Chainlink LST/ETH feeds (stETH/ETH, ETHx/ETH) have heartbeat intervals of 1–24 hours and deviation thresholds of 0.5–1%. A feed can go stale without oracle operator action during network congestion or a period where the price has not moved enough to trigger a deviation update. An attacker monitoring on-chain `updatedAt` values can detect staleness and act within the same block. No privileged access is required to call `instantWithdrawal` — only rsETH balance and the asset having `isInstantWithdrawalEnabled[asset] == true`.

## Recommendation

1. **Add a staleness check in `ChainlinkPriceOracle.getAssetPrice`** (`contracts/oracles/ChainlinkPriceOracle.sol`, line 52):

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound)
    = priceFeed.latestRoundData();
require(answeredInRound >= roundId, "Stale round");
require(block.timestamp - updatedAt <= STALENESS_THRESHOLD, "Stale price");
require(price > 0, "Non-positive price");
```

2. **Add price-bounds validation to `instantWithdrawal`** (`contracts/LRTWithdrawalManager.sol`), mirroring the `_validatePrices` pattern already used in `unlockQueue`. Accept caller-supplied or governance-configured min/max bounds for both `rsETHPrice` and `assetPrice` and revert if either is outside the window.

## Proof of Concept

Fork-test outline (Foundry, mainnet fork):

```solidity
// 1. Deploy a MockAggregator that returns a price 30% below the real stETH/ETH rate
//    and an updatedAt timestamp 25 hours in the past.
MockAggregator staleFeed = new MockAggregator(
    realPrice * 70 / 100,   // deflated by 30%
    block.timestamp - 25 hours
);

// 2. Simulate a stale feed condition (in production, attacker waits for real staleness).
//    For fork-test purposes only: chainlinkOracle.updatePriceFeedFor(stETH, address(staleFeed));

// 3. Attacker holds rsETHUnstaked worth fairValue ETH at the real price.
uint256 rsETHUnstaked = 1 ether;
uint256 fairAssetAmount = rsETHUnstaked * rsETHPrice / realAssetPrice;

// 4. Call instantWithdrawal — no staleness revert, no price-bounds revert.
withdrawalManager.instantWithdrawal(stETH, rsETHUnstaked, "");

// 5. Assert attacker received more than fair value.
uint256 received = stETH.balanceOf(attacker);
// received ≈ rsETHUnstaked * rsETHPrice / (realPrice * 0.70)
//           = fairAssetAmount / 0.70  ≈ fairAssetAmount * 1.43
assert(received > fairAssetAmount);
// Profit ≈ 43% of fairAssetAmount, minus instantWithdrawalFee.
```

The assertion passes on unmodified production code because `ChainlinkPriceOracle.getAssetPrice` never checks `updatedAt` and `instantWithdrawal` never validates the price against any bounds. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L228-235)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);
```

**File:** contracts/LRTWithdrawalManager.sol (L288-295)
```text
        _validatePrices(
            params.rsETHPrice,
            params.assetPrice,
            minimumRsEthPrice,
            maximumRsEthPrice,
            minimumAssetPrice,
            maximumAssetPrice
        );
```

**File:** contracts/LRTWithdrawalManager.sol (L590-593)
```text
        ILRTOracle lrtOracle = ILRTOracle(lrtConfig.getContract(LRTConstants.LRT_ORACLE));

        // calculate underlying asset amount to receive based on rsETH amount and asset exchange rate
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTWithdrawalManager.sol (L853-870)
```text
    function _validatePrices(
        uint256 rsETHPrice,
        uint256 assetPrice,
        uint256 minimumRsEthPrice,
        uint256 maximumRsEthPrice,
        uint256 minimumAssetPrice,
        uint256 maximumAssetPrice
    )
        internal
        pure
    {
        if (rsETHPrice < minimumRsEthPrice || rsETHPrice > maximumRsEthPrice) {
            revert RsETHPriceOutOfPriceRange(rsETHPrice);
        }
        if (assetPrice < minimumAssetPrice || assetPrice > maximumAssetPrice) {
            revert AssetPriceOutOfPriceRange(assetPrice);
        }
    }
```
