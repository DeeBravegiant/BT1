Audit Report

## Title
Chainlink `minAnswer` Circuit Breaker Not Validated in `getAssetPrice()`, Enabling Deposit at Inflated Price - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and uses the raw returned price with no validation against the aggregator's `minAnswer`/`maxAnswer` bounds. When a supported LST asset crashes below the Chainlink circuit-breaker floor, the oracle silently returns `minAnswer` instead of the real price. Because `LRTDepositPool.getRsETHAmountToMint()` divides the live (inflated) oracle price by the stored `rsETHPrice`, an attacker can deposit the crashed asset and receive rsETH at a grossly inflated valuation, then redeem for healthy assets — constituting direct theft of funds from honest depositors.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice()` (L49–55) calls `latestRoundData()` and returns the raw answer with no bounds check:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Chainlink aggregators enforce a `minAnswer` floor. When an asset's market price falls below this floor, the aggregator does not revert — it returns `minAnswer`. The protocol has no mechanism to detect this condition.

This price is consumed directly in `LRTDepositPool.getRsETHAmountToMint()` (L519–520):

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

Here, `lrtOracle.getAssetPrice(asset)` returns the live (inflated) Chainlink price, while `lrtOracle.rsETHPrice()` returns the **stored** rsETH price — which has not yet been updated to reflect the crash. The ratio is therefore inflated by the factor `minAnswer / realPrice`.

The `pricePercentageLimit` guard in `_updateRsETHPrice()` (L252–282) does **not** protect this path. It only applies when `updateRSETHPrice()` is called; the deposit calculation uses the live oracle price directly and independently of any price-update call. The attacker never needs to call `updateRSETHPrice()`.

The same missing bounds check exists in `ChainlinkOracleForRSETHPoolCollateral.getRate()` (L27–36), which checks `ethPrice <= 0` but not against `minAnswer`/`maxAnswer`, and in `RSETHPriceFeed.latestRoundData()` (L63–70), which passes through the raw ETH/USD answer without bounds checking.

## Impact Explanation

**Critical — Direct theft of user funds at rest.**

An attacker deposits a crashed LST at the inflated `minAnswer` price and receives rsETH representing a claim on the full protocol portfolio (including healthy assets). On redemption, the attacker extracts healthy assets worth far more than the crashed tokens deposited. Honest depositors' holdings are diluted. The attack is fully permissionless via `depositAsset()`.

## Likelihood Explanation

Chainlink `minAnswer` circuit breakers are a known, documented behavior with a real historical precedent (Venus/LUNA on BSC). The LRT-rsETH protocol supports multiple LST assets, each with its own Chainlink feed and its own `minAnswer`. A severe de-peg or collapse of any single supported asset is sufficient to trigger this path. The deposit function is public and callable by any unprivileged user. **Likelihood: Medium** (requires an external asset crash event, but the exploit is fully permissionless once it occurs and requires no special access).

## Recommendation

In `ChainlinkPriceOracle.getAssetPrice()`, after calling `latestRoundData()`, retrieve the aggregator's `minAnswer` and `maxAnswer` from `AggregatorV2V3Interface` and revert if the returned price is at or outside those bounds:

```solidity
interface IFeedWithBounds {
    function minAnswer() external view returns (int192);
    function maxAnswer() external view returns (int192);
}

// In getAssetPrice():
(, int256 price,,,) = priceFeed.latestRoundData();
int192 minAns = IFeedWithBounds(address(priceFeed)).minAnswer();
int192 maxAns = IFeedWithBounds(address(priceFeed)).maxAnswer();
if (price <= int256(minAns) || price >= int256(maxAns)) revert OracleAtCircuitBreaker();
```

Apply the same fix to `ChainlinkOracleForRSETHPoolCollateral.getRate()` and `RSETHPriceFeed.latestRoundData()`.

## Proof of Concept

1. Assume stETH is a supported asset with a Chainlink feed whose `minAnswer` = `0.5e18` (0.5 ETH).
2. stETH de-pegs catastrophically; real market price = `0.001e18` ETH.
3. Chainlink aggregator hits circuit breaker; `latestRoundData()` returns `answer = 0.5e18`.
4. Attacker buys 1000 stETH on the open market for ~1 ETH worth of value.
5. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
6. `getRsETHAmountToMint()` computes: `(1000e18 * 0.5e18) / rsETHPrice`. With `rsETHPrice ≈ 1e18`, this mints ~500 rsETH.
7. The real value deposited is ~1 ETH; the attacker receives rsETH representing a ~500 ETH claim.
8. Attacker redeems rsETH via the withdrawal manager for rETH or other healthy assets.
9. Attacker extracts ~500 ETH worth of healthy assets having spent ~1 ETH — draining honest depositors.

**Foundry fork test plan:**
- Fork mainnet with a stETH/ETH Chainlink feed.
- Mock the feed to return `minAnswer` while the real price is 500× lower.
- Call `depositAsset(stETH, 1000e18, 0, "")` from an attacker address.
- Assert `rsethAmountToMint` is ~500× the fair value.
- Assert the attacker can redeem for healthy assets exceeding their deposit value.