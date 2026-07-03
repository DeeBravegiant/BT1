Audit Report

## Title
Missing Staleness Validation in `ChainlinkPriceOracle.getAssetPrice()` Allows Stale Price to Corrupt rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards the `updatedAt` timestamp and `answeredInRound` fields, accepting stale Chainlink prices without any validation. This stale price propagates through `LRTOracle._getTotalEthInProtocol()` into the protocol-wide `rsETHPrice` storage variable, which governs rsETH minted per deposit and ETH returned per withdrawal. The update path is publicly callable by any address via `LRTOracle.updateRSETHPrice()`.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the LST/ETH rate as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The `updatedAt` (position 4) and `answeredInRound` (position 5) return values are discarded. No maximum-age check and no round-completeness check are performed. This directly contrasts with `ChainlinkOracleForRSETHPoolCollateral.sol`, a sibling contract in the same repository, which correctly validates all three conditions before using the price:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The stale price flows through the following confirmed call chain:

1. `ChainlinkPriceOracle.getAssetPrice(asset)` — returns stale LST/ETH price (L52–54)
2. `LRTOracle.getAssetPrice(asset)` — delegates to the above (L156–158)
3. `LRTOracle._getTotalEthInProtocol()` — sums all asset values using stale prices (L339)
4. `LRTOracle._updateRsETHPrice()` — computes and stores `rsETHPrice` from the stale total (L250, L313)
5. `LRTOracle.updateRSETHPrice()` — **publicly callable with no access restriction** (L87)

The `pricePercentageLimit` guard in `_updateRsETHPrice()` (L252–266, L270–281) is a partial mitigation only: it defaults to `0` (disabled), and even when set, it only blocks deviations exceeding the configured threshold — a stale price within the threshold passes silently.

## Impact Explanation

**Scenario A — Stale HIGH price (e.g., Chainlink circuit breaker holds stETH/ETH at 1.0 while market price drops to 0.95):**
- `totalETHInProtocol` is inflated → `rsETHPrice` is set above its true value
- Withdrawers burn rsETH at the inflated rate and receive more ETH than the protocol actually holds → direct theft of funds from other depositors
- This constitutes **Critical: Direct theft of user funds at rest**

**Scenario B — Stale LOW price (e.g., feed lags during a recovery):**
- `rsETHPrice` is deflated → depositors receive excess rsETH → permanent dilution of existing rsETH holders' yield
- This constitutes **High: Theft of unclaimed yield**

The SECURITY.md exclusion for "Incorrect data supplied by third-party oracles" does not apply here: the bug is the contract's failure to validate oracle data, not the oracle itself being compromised. The note in SECURITY.md explicitly states this exclusion "does not exclude oracle manipulation/flash-loan attacks," and missing staleness validation is a contract-level defect, not an oracle-level failure.

## Likelihood Explanation

Chainlink feeds are known to go stale during extreme market events due to deviation-threshold-based update models, min/max circuit breakers, and network congestion. The stETH/ETH feed has historically exhibited staleness during depeg events. The trigger (`updateRSETHPrice()`) is publicly callable by any address with no access restriction, so an attacker can deliberately call it at the moment a stale price is most advantageous and immediately withdraw. The attack requires no privileged access, no victim mistakes, and no external protocol compromise beyond the Chainlink feed going stale — a known, recurring condition.

## Recommendation

Apply the same staleness checks already present in `ChainlinkOracleForRSETHPoolCollateral.sol` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
uint256 public constant MAX_STALENESS_PERIOD = 3600; // configurable

function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > MAX_STALENESS_PERIOD) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

## Proof of Concept

1. Chainlink stETH/ETH feed goes stale at price `1.0e18` while the actual market price drops to `0.95e18` (5% depeg). The feed's `updatedAt` timestamp is now older than `MAX_STALENESS_PERIOD` but the contract never checks it.
2. Attacker observes the stale feed on-chain (verifiable via `latestRoundData()` directly).
3. Attacker calls `LRTOracle.updateRSETHPrice()` (no access restriction, `public whenNotPaused`).
   - `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice()` for stETH, which returns the stale `1.0e18` price.
   - `totalETHInProtocol` is inflated by ~5%.
   - `newRsETHPrice` is set ~5% above its true value and stored in `rsETHPrice`.
   - If `pricePercentageLimit` is 0 (default), no revert occurs.
4. Attacker immediately initiates a withdrawal via `LRTWithdrawalManager`, burning rsETH at the inflated `rsETHPrice`.
5. Attacker receives ~5% more ETH than the protocol's actual backing, extracting value from other depositors.

**Foundry fork test plan:** Fork mainnet, mock the stETH/ETH Chainlink aggregator to return a stale `updatedAt` (e.g., `block.timestamp - 7200`) with price `1e18`, call `LRTOracle.updateRSETHPrice()` as an unprivileged address, assert `rsETHPrice` is inflated relative to a fresh-price baseline, then call the withdrawal path and assert the attacker receives excess ETH.