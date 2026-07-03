Audit Report

## Title
Missing Chainlink Oracle Output Validation Enables Stale Price Consumption During rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all staleness indicators, accepting any price — including stale or zero values — without validation. This output feeds directly into rsETH minting in `LRTDepositPool`, allowing a depositor to receive excess rsETH when a Chainlink feed is stale at an inflated price, diluting all existing rsETH holders. The same repository already implements the correct validation pattern in `ChainlinkOracleForRSETHPoolCollateral`, making this a clear and demonstrable inconsistency.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the price as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All five return values are available but only `answer` is used. The following checks are absent:

- **No stale round check**: `answeredInRound < roundId` is never verified.
- **No timestamp check**: `updatedAt` is never compared to `block.timestamp` or checked for zero.
- **No positive price check**: `price` is cast directly to `uint256` without verifying `price > 0`.

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repository correctly validates all three conditions:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The vulnerable oracle is registered as the price oracle for supported LST assets via `LRTOracle.assetPriceOracle`. Its output flows directly into rsETH minting:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

`lrtOracle.getAssetPrice(asset)` delegates to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)`: [4](#0-3) 

**Existing mitigations are insufficient:**

1. `LRTOracle.updatePriceOracleForValidated()` checks that the oracle price is between `1e16` and `1e19` at setup time only — it does not prevent a stale price from being consumed during subsequent deposits. [5](#0-4) 

2. The `pricePercentageLimit` guard in `_updateRsETHPrice()` applies to rsETH price updates, not to individual asset price fetches during minting. [6](#0-5) 

3. The depositor's `minRSETHAmountExpected` slippage parameter protects the depositor from receiving *less* than expected, but does not prevent over-minting at the expense of existing holders. [7](#0-6) 

**Note on the negative price scenario:** In Solidity 0.8.x, `uint256(price) * 1e18` where `price` is negative would overflow and revert due to built-in overflow protection. The "catastrophic minting" claim for negative prices is therefore not exploitable as described — it would cause a revert (DoS) rather than unbounded minting. The stale price scenario is the primary valid attack vector.

## Impact Explanation
**Critical — Direct theft of user funds.**

When a Chainlink feed goes stale at an inflated price, any depositor calling `depositAsset()` receives more rsETH than their fair proportional share of the underlying TVL. This dilutes all existing rsETH holders' claims on protocol assets, constituting direct theft of their proportional share of the TVL. The harm is permanent: once excess rsETH is minted and the attacker redeems it, existing holders cannot recover their diluted share.

## Likelihood Explanation
Chainlink feeds going stale is a documented, recurring real-world event — it has occurred during Ethereum network congestion, L2 sequencer outages, and feed deprecations. The `answeredInRound < roundId` staleness condition is a standard Chainlink-documented check that the protocol itself applies in `ChainlinkOracleForRSETHPoolCollateral` but omits in `ChainlinkPriceOracle`. Any unprivileged depositor can trigger this path by simply calling `depositAsset()` on `LRTDepositPool` when the feed is stale — no special privileges, flash loans, or governance access required.

## Recommendation
Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optionally: if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

## Proof of Concept
1. Assume the stETH/ETH Chainlink feed goes stale at price `2e18` (2 ETH per stETH) while the true market price is `1e18`.
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 1e18, 0, "")`, depositing 1 stETH.
3. `getRsETHAmountToMint` computes: `(1e18 * 2e18) / rsETHPrice`. With `rsETHPrice ≈ 1e18`, result is `2e18` rsETH.
4. Attacker receives 2 rsETH for 1 stETH worth of collateral — double the fair amount.
5. Attacker redeems 2 rsETH via the withdrawal system, extracting 2× the deposited value from the protocol's TVL at the expense of existing rsETH holders.

**Foundry fork test plan:**
- Fork mainnet at a block where a Chainlink feed's `answeredInRound < roundId` (or mock a stale feed returning an inflated price).
- Call `depositAsset()` with a small amount and assert that `rsethAmountToMint` exceeds the fair value.
- Confirm that `ChainlinkOracleForRSETHPoolCollateral.getRate()` would revert on the same feed state, demonstrating the inconsistency.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L667-669)
```text
        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/LRTOracle.sol (L101-108)
```text
    function updatePriceOracleForValidated(address asset, address priceOracle) external onlyLRTAdmin {
        // Sanity check: oracle price must have precision between 1e16 and 1e19
        uint256 price = IPriceFetcher(priceOracle).getAssetPrice(asset);
        if (price > 1e19 || price < 1e16) {
            revert InvalidPriceOracle();
        }
        updatePriceOracleFor(asset, priceOracle);
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```
