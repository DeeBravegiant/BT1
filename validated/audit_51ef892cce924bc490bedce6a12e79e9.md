Audit Report

## Title
Missing Staleness Checks on Chainlink `latestRoundData()` Enables Excess rsETH Minting at Existing Holders' Expense - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary

`ChainlinkPriceOracle.getAssetPrice()` discards all `latestRoundData()` return values except `answer`, performing no round-staleness or validity checks. A stale (deflated) price for any one LST asset causes `_updateRsETHPrice()` to write a deflated `rsETHPrice` to storage. Because `updateRSETHPrice()` is public and permissionless, an attacker can trigger this during a natural staleness window and immediately deposit a *different*, correctly-priced asset to receive excess rsETH, diluting all existing holders.

## Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches price data as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

The `roundId`, `updatedAt`, and `answeredInRound` fields are silently discarded. No `answeredInRound < roundId` check, no `updatedAt == 0` check, and no `price <= 0` check are performed. The sibling contract in the same repository, `ChainlinkOracleForRSETHPoolCollateral`, applies all three guards:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The stale price flows into `LRTOracle._getTotalEthInProtocol()`, which multiplies each asset's total deposited balance by its (potentially stale) oracle price: [3](#0-2) 

This total is used in `_updateRsETHPrice()` to compute and store `rsETHPrice`: [4](#0-3) 

`updateRSETHPrice()` is public with no role restriction: [5](#0-4) 

The stored `rsETHPrice` is then used as the denominator in `getRsETHAmountToMint()`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [6](#0-5) 

**Exploit mechanics (multi-asset pool):** Suppose the pool holds assets A (stale feed, deflated by δ) and B (fresh feed). After `updateRSETHPrice()` is called, `rsETHPrice` is deflated because asset A's contribution to `totalETHInProtocol` is understated. When the attacker then deposits asset B, `getAssetPrice(B)` returns the correct live price while `rsETHPrice` remains deflated, so the ratio `getAssetPrice(B) / rsETHPrice` is inflated and the attacker receives excess rsETH. The (1-δ) factor does *not* cancel when depositing the non-stale asset — it only cancels if the attacker deposits the same stale asset in a single-asset pool.

**Why the `pricePercentageLimit` guard is insufficient:** The downside protection at `_updateRsETHPrice()` only pauses the protocol when the price drop *exceeds* `pricePercentageLimit`: [7](#0-6) 

Small or moderate staleness (within the configured threshold) passes through silently. When `pricePercentageLimit == 0`, no protection exists at all.

## Impact Explanation

**High — Theft of unclaimed yield.** Existing rsETH holders' accrued yield (the ETH-denominated appreciation of their rsETH) is diluted. The attacker's excess rsETH represents a claim on more underlying ETH than they contributed, extracted from the pool at the expense of all prior depositors. The loss is proportional to the staleness magnitude and the fraction of pool TVL held in the stale asset.

## Likelihood Explanation

Chainlink LST/ETH feeds (stETH/ETH, rETH/ETH, cbETH/ETH) have historically experienced brief staleness windows during network congestion. `updateRSETHPrice()` requires no role — any address can call it. The attacker needs only to observe a naturally occurring staleness event on-chain and act within the same block or shortly after. No oracle manipulation, governance capture, or privileged access is required. Likelihood is **medium**.

## Recommendation

Apply the same staleness and validity checks already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optional per-feed heartbeat: if (block.timestamp - updatedAt > HEARTBEAT) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

## Proof of Concept

**Setup:** Pool holds two assets — stETH (Chainlink feed goes stale, returning price 2% below true value) and rETH (fresh feed). `pricePercentageLimit` is 0 or the 2% drop is within the configured threshold.

1. Chainlink stETH/ETH feed becomes stale on-chain; `latestRoundData()` returns a price 2% below true value.
2. Attacker calls `LRTOracle.updateRSETHPrice()` (no role required). `_getTotalEthInProtocol()` uses the stale stETH price, computing `totalETHInProtocol` ~1% below true value (assuming equal TVL split). `rsETHPrice` is written to storage at the deflated value.
3. Attacker calls `LRTDepositPool.depositAsset(rETH, amount, 0, "")`. Inside `getRsETHAmountToMint()`: `getAssetPrice(rETH)` returns the correct live rETH price; `rsETHPrice` is the deflated stored value. The attacker receives ~1% more rsETH than fair value.
4. When the stETH feed recovers and `updateRSETHPrice()` is called again, `rsETHPrice` rises back to true value. The attacker's excess rsETH now represents a claim on more ETH than deposited, extracted from existing holders.

**Foundry fork test plan:** Fork mainnet, mock `latestRoundData()` on the stETH/ETH Chainlink feed to return a stale answer (e.g., `answeredInRound < roundId` or `updatedAt` set to a past timestamp). Call `updateRSETHPrice()` as an unprivileged address. Record `rsETHPrice`. Call `depositAsset(rETH, amount)`. Assert that `rsethAmountToMint > amount * true_rETH_price / true_rsETHPrice`, confirming excess minting.

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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L273-281)
```text
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
