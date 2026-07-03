Audit Report

## Title
Missing Chainlink Staleness Checks Enable rsETH Mispricing and Protocol Insolvency — (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards `updatedAt` and `answeredInRound`, accepting stale prices without validation. A stale (inflated) price for any supported LST propagates through `LRTOracle._getTotalEthInProtocol()` into `rsETHPrice`, allowing an unprivileged attacker to mint rsETH at an over-valued rate via the public `updateRSETHPrice()` function, then redeem at the corrected rate, extracting ETH from the protocol's collateral pool.

## Finding Description

**Root cause — `contracts/oracles/ChainlinkPriceOracle.sol` line 52:**

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
```

All five return values (`roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound`) are available but only `price` is consumed. `updatedAt` and `answeredInRound` are silently discarded. [1](#0-0) 

**Propagation path:**

1. `LRTOracle.getAssetPrice(asset)` delegates to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)`, which resolves to `ChainlinkPriceOracle.getAssetPrice()`. [2](#0-1) 

2. `_getTotalEthInProtocol()` iterates all supported assets, multiplies each asset's total deposits by its (potentially stale) price, and sums to produce `totalETHInProtocol`. [3](#0-2) 

3. `_updateRsETHPrice()` divides `totalETHInProtocol` by `rsethSupply` to set `rsETHPrice`. [4](#0-3) 

4. `updateRSETHPrice()` is **public with no access control** beyond `whenNotPaused`, so any EOA can trigger it. [5](#0-4) 

5. `LRTDepositPool.getRsETHAmountToMint()` mints rsETH using `getAssetPrice(asset) / rsETHPrice`. Both values are read at deposit time; `rsETHPrice` is the stored value from the last `updateRSETHPrice()` call. [6](#0-5) 

**Why the partial-cancellation argument fails:**

If asset A (stale, 2× inflated) represents fraction `f` of total TVL:
- `rsETHPrice` inflates by factor `(1 + f)` (weighted average)
- Numerator in mint formula inflates by `2×`
- Net rsETH minted ≈ `2 / (1 + f)` × correct amount

For `f = 0.1` (10% of TVL), attacker receives ~1.82× the correct rsETH. After the feed corrects and `updateRSETHPrice()` is called again, the attacker's rsETH redeems for ~82% more ETH than deposited, at the expense of existing depositors.

**Partial mitigation — `pricePercentageLimit` — is ineffective in default state:**

`_updateRsETHPrice()` checks whether the new price exceeds `highestRsethPrice` by more than `pricePercentageLimit`, reverting for non-managers if so. [7](#0-6) 

However, `pricePercentageLimit` is **not initialized** in `initialize()` and defaults to `0`. [8](#0-7) 

When it is `0`, the guard condition `pricePercentageLimit > 0 && ...` is always `false`, meaning **no price-increase protection exists** in the default deployment state. [9](#0-8) 

The same `pricePercentageLimit > 0` short-circuit also disables the downside protection that would pause the protocol on price correction, meaning the attacker can freely redeem after the feed corrects. [10](#0-9) 

## Impact Explanation

An attacker can mint rsETH at an inflated rate and redeem at the corrected rate, extracting real ETH from the protocol's collateral pool. Repeated or large-scale exploitation drains collateral below the rsETH supply, causing **protocol insolvency** (rsETH becomes undercollateralized). Existing depositors suffer permanent loss of funds. This matches the **Critical — Protocol insolvency** impact class.

## Likelihood Explanation

Chainlink feeds can go stale due to network congestion, sequencer downtime (on L2), or Chainlink node issues — these are well-documented, non-adversarial conditions. The attack requires no privileged access: only a public call to `updateRSETHPrice()` and a standard `depositAsset()` call. The window of opportunity exists for as long as the feed remains stale. The default `pricePercentageLimit = 0` means no on-chain guard is active unless an admin has explicitly configured it post-deployment, making the default deployment fully unprotected.

## Recommendation

Add staleness and round-completeness checks in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price, , uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(price > 0, "Invalid price");
require(answeredInRound >= roundId, "Stale round");
require(block.timestamp - updatedAt <= MAX_STALENESS, "Stale price");
```

`MAX_STALENESS` should be set per-feed based on the feed's documented heartbeat (e.g., 3600 seconds for a 1-hour heartbeat feed). Additionally, ensure `pricePercentageLimit` is initialized to a non-zero value during deployment or in `initialize()`.

## Proof of Concept

```solidity
// Foundry fork test (local fork)
// 1. Deploy mock Chainlink feed for stETH returning 2× real price
// 2. Set mock feed in ChainlinkPriceOracle via updatePriceFeedFor (manager)
// 3. Call LRTOracle.updateRSETHPrice() as attacker (public, no role required)
//    → rsETHPrice is now inflated by factor (1 + f) where f = stETH fraction of TVL
// 4. Attacker deposits 1 stETH via LRTDepositPool.depositAsset()
//    → receives ~1.82× correct rsETH (assuming stETH = 10% of TVL)
// 5. Restore real feed price (simulate staleness window ending)
// 6. Call LRTOracle.updateRSETHPrice() (anyone can call, pricePercentageLimit=0 so no pause)
//    → rsETHPrice corrects downward
// 7. Attacker redeems rsETH via withdrawal flow
//    → receives ~1.82 stETH worth of ETH for 1 stETH deposited
// 8. Assert: attacker ETH out > attacker ETH in → insolvency confirmed
// Key invariant to fuzz: sum(rsETH * rsETHPrice) <= totalETHInProtocol
```

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/LRTOracle.sol (L64-68)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
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

**File:** contracts/LRTOracle.sol (L273-274)
```text
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTOracle.sol (L336-344)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
