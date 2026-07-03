Audit Report

## Title
Missing `price <= 0` Validation and Chainlink `minAnswer`/`maxAnswer` Circuit Breaker Check in `ChainlinkPriceOracle.getAssetPrice()` - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and immediately casts the returned `int256 price` to `uint256` with no positivity check and no validation against Chainlink's built-in `minAnswer`/`maxAnswer` circuit breaker bounds. If a supported LST asset's real market price falls below the aggregator's `minAnswer`, the feed silently returns the clamped floor value, inflating `totalETHInProtocol` and `rsETHPrice`, enabling an attacker to deposit the devalued asset and extract real value from honest rsETH holders.

## Finding Description

**Root cause — `ChainlinkPriceOracle.getAssetPrice()` (L49–55):**

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,,,) = priceFeed.latestRoundData();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Two defects:
1. **No `price <= 0` guard.** A zero price returns 0 (asset valued at nothing). A negative price — possible if the aggregator malfunctions — is silently reinterpreted as a near-`2^256` value via two's complement, catastrophically inflating the oracle output.
2. **No `minAnswer`/`maxAnswer` guard.** Chainlink aggregators clamp their answer to a pre-configured band. When the real market price falls below `minAnswer`, the feed returns `minAnswer` indefinitely. The contract has no mechanism to detect or reject this clamped value.

**Propagation path:**

- `LRTOracle.getAssetPrice()` (L156–158) delegates directly to `ChainlinkPriceOracle.getAssetPrice()`.
- `_getTotalEthInProtocol()` (L336–343) multiplies each asset's on-chain balance by its oracle price; a clamped `minAnswer` inflates `totalETHInProtocol`.
- `_updateRsETHPrice()` (L250) divides the inflated total by `rsethSupply`, setting `rsETHPrice` above its true backing value.

**Why existing guards fail:**

The downside protection in `_updateRsETHPrice()` (L270–281) only triggers when `newRsETHPrice < highestRsethPrice`. Because the oracle is returning an *inflated* `minAnswer`, `totalETHInProtocol` is overstated and `newRsETHPrice` appears equal to or above the historical peak — the downside circuit breaker never fires.

The upside `pricePercentageLimit` check (L252–266) only fires if `pricePercentageLimit > 0` AND the new price exceeds `highestRsethPrice`. If `pricePercentageLimit` is zero (its default), or if the asset was already at `minAnswer` before the crash event, this guard is also bypassed.

**Note on secondary instance:** The claim states `ChainlinkOracleForRSETHPoolCollateral.getRate()` also lacks a `price <= 0` check. This is factually incorrect — the code at L32 does include `if (ethPrice <= 0) revert InvalidPrice();`. However, `getRate()` still lacks `minAnswer`/`maxAnswer` validation, which is a separate, lower-severity gap in the pool swap pricing path.

## Impact Explanation

**Critical — Direct theft of user funds.**

When a supported LST asset's real price falls below Chainlink's `minAnswer`:
1. `rsETHPrice` is set above its true backing value.
2. An attacker deposits the near-worthless asset and receives rsETH minted at the inflated rate — far more rsETH than the real backing warrants.
3. The attacker redeems rsETH for ETH or other assets, extracting real value.
4. Remaining rsETH holders are left holding tokens backed by less ETH than the price implies — a direct, permanent loss of funds.

This matches the allowed impact: **Critical — Direct theft of any user funds.**

## Likelihood Explanation

Chainlink circuit breakers are a documented, immutable property of every aggregator contract. The Venus/LUNA incident on BSC is a confirmed real-world precedent where this exact mechanism was exploited. The LRT-rsETH protocol supports multiple LST assets (stETH, cbETH, rETH), each with its own Chainlink feed and its own `minAnswer`. A severe depeg of any single supported asset — as has occurred historically with stETH during the Merge period and with other LSTs — is sufficient to trigger this path. No privileged access is required; any depositor can exploit it the moment the real price falls below `minAnswer` and `updateRSETHPrice()` is called (which is a public, permissionless function).

## Recommendation

In `ChainlinkPriceOracle.getAssetPrice()`, after calling `latestRoundData()`:

1. Revert if `price <= 0`.
2. Cast the aggregator address to `AggregatorV2V3Interface` (which exposes `minAnswer()` and `maxAnswer()`), and revert if `price <= minAnswer || price >= maxAnswer`.

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= 0) revert InvalidPrice();
int192 minAnswer = AggregatorV2V3Interface(address(priceFeed)).minAnswer();
int192 maxAnswer = AggregatorV2V3Interface(address(priceFeed)).maxAnswer();
if (price <= minAnswer || price >= maxAnswer) revert PriceOutOfBounds();
```

Apply the same `minAnswer`/`maxAnswer` bounds check to `ChainlinkOracleForRSETHPoolCollateral.getRate()` for the ETH/USD feed.

## Proof of Concept

**Setup:** stETH is a supported asset. Its Chainlink stETH/ETH aggregator has `minAnswer = 0.5e18`. The real market price of stETH crashes to 0.05 ETH.

**Call sequence:**

1. Chainlink's stETH/ETH aggregator clamps its answer to `minAnswer = 0.5e18`.
2. Anyone calls `LRTOracle.updateRSETHPrice()` (public, permissionless).
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)`, which returns `0.5e18` — 10× the real price.
4. `totalETHInProtocol` is inflated by `stETHBalance × 0.45e18` (the phantom value).
5. `rsETHPrice` is set to the inflated value; the downside circuit breaker does not fire because the oracle-reported price has not decreased.
6. Attacker calls `LRTDepositPool.depositAsset(stETH, largeAmount)`, receiving rsETH priced at the inflated oracle rate.
7. Attacker redeems rsETH for ETH or other non-devalued assets, extracting real value. Honest holders are left with under-backed rsETH.

**Foundry fork test plan:**
- Fork mainnet at a block where stETH is a supported asset.
- Deploy a mock Chainlink aggregator with `minAnswer = 0.5e18` that always returns `0.5e18`.
- Register it as the stETH price feed via `ChainlinkPriceOracle.updatePriceFeedFor`.
- Call `LRTOracle.updateRSETHPrice()` and assert `rsETHPrice > true_backing_price`.
- Execute `depositAsset(stETH, amount)` as attacker and assert rsETH minted exceeds fair share.
- Redeem and assert net ETH extracted exceeds ETH deposited.