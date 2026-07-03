Audit Report

## Title
Missing Staleness and Zero-Value Validation in Chainlink Price Feed — (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `price`, performing no staleness, zero-value, or incomplete-round validation. A stale or zero Chainlink answer propagates unchecked into `LRTOracle._updateRsETHPrice()`, which is callable by any unprivileged user via the public `updateRSETHPrice()`. If the resulting artificial price drop exceeds `pricePercentageLimit`, the protocol's downside-protection logic automatically pauses `lrtDepositPool`, `withdrawalManager`, and `LRTOracle`, temporarily freezing all user deposits and withdrawals.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` reads the feed as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All five return values (`roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound`) are available but only `price` is used. No check is made for `answeredInRound >= roundId`, `updatedAt != 0`, `block.timestamp - updatedAt <= heartbeat`, or `price > 0`.

By contrast, the sibling contract `ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repository correctly performs all three checks: [2](#0-1) 

The unchecked price flows through: `updateRSETHPrice()` (public, `whenNotPaused` only) → `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice()`. [3](#0-2) [4](#0-3) 

A stale price (e.g., 10% below true market) or a zero price (circuit-breaker) causes `_getTotalEthInProtocol()` to undercount TVL, producing an artificially depressed `newRsETHPrice`. The downside-protection block then fires: [5](#0-4) 

This pauses `lrtDepositPool`, `withdrawalManager`, and `LRTOracle` itself, blocking all user deposits and withdrawals until an admin manually unpauses.

## Impact Explanation
**Medium — Temporary freezing of funds.** Any unprivileged external caller can invoke `updateRSETHPrice()` at any time the contract is not already paused. When a registered Chainlink feed returns a stale or zero price, the auto-pause fires deterministically if `pricePercentageLimit > 0` (a standard production configuration). All user deposits and withdrawals are frozen until an admin calls `unpause()`. Funds are not lost, making this a temporary rather than permanent freeze.

## Likelihood Explanation
Chainlink feeds have documented heartbeat intervals (24 h for some ETH-denominated pairs, 1 h for others). During low-volatility periods or L2 sequencer outages, feeds can go stale without any adversarial action. Once stale, any external actor can call the public `updateRSETHPrice()` to trigger the incorrect price update and resulting pause. A zero price (Chainlink circuit-breaker) produces the same outcome with certainty. The condition is non-adversarial, repeatable, and requires no special privileges.

## Recommendation
Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral.getRate()` to `ChainlinkPriceOracle.getAssetPrice()`:

1. Store a per-asset `heartbeat` (maximum acceptable staleness) alongside `assetPriceFeed`.
2. After calling `latestRoundData()`, enforce:
   - `answeredInRound >= roundId` (no incomplete round)
   - `updatedAt != 0` (round is complete)
   - `block.timestamp - updatedAt <= heartbeat` (price is fresh)
   - `price > 0` (valid answer)

This mirrors the fix already implemented in `ChainlinkOracleForRSETHPoolCollateral` and eliminates the inconsistency between the two oracle contracts. [6](#0-5) 

## Proof of Concept
1. A Chainlink feed registered in `ChainlinkPriceOracle` (e.g., stETH/ETH) goes stale — `updatedAt` is older than the feed's heartbeat, and the returned price is 10% below the true market price.
2. An unprivileged attacker calls `LRTOracle.updateRSETHPrice()`.
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)`, which returns the stale depressed price with no revert.
4. `newRsETHPrice` is computed as artificially low.
5. `(highestRsethPrice - newRsETHPrice) > pricePercentageLimit * highestRsethPrice` evaluates to `true`.
6. `lrtDepositPool.pause()`, `withdrawalManager.pause()`, and `_pause()` are called, freezing all user deposits and withdrawals.
7. All funds remain frozen until an admin calls `unpause()`.

**Foundry fork test plan:** Fork mainnet/L2 at a block where a registered feed's `updatedAt` is beyond its heartbeat. Call `LRTOracle.updateRSETHPrice()` from an unprivileged EOA. Assert that `lrtDepositPool.paused()`, `withdrawalManager.paused()`, and `LRTOracle.paused` all return `true` after the call.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L270-281)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
```

**File:** contracts/LRTOracle.sol (L336-343)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
