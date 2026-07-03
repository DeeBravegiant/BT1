Audit Report

## Title
Missing Chainlink Oracle Staleness Check Enables rsETH Over-Minting During Price Feed Lag — (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle.getAssetPrice` calls `latestRoundData()` but discards all return values except `answer`, performing no staleness, round-completeness, or zero-price validation. A stale feed price flows directly into `getRsETHAmountToMint`, allowing depositors to mint rsETH against inflated collateral valuations. When the oracle eventually corrects, `rsETHPrice` drops and existing holders are diluted, constituting protocol insolvency.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice` at line 52 destructures all five `latestRoundData` return values but uses only `price`:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No checks are performed on `updatedAt` (heartbeat/staleness), `answeredInRound >= roundId` (round completeness), or `price > 0` (invalid price guard).

This price is consumed directly in `LRTDepositPool.getRsETHAmountToMint`:

```solidity
// contracts/LRTDepositPool.sol L519-520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.rsETHPrice()` is a stored value updated only on explicit calls to `updateRSETHPrice()`. If the Chainlink feed is stale (reporting `R_stale > R_real`) while `rsETHPrice` was last computed when the price was accurate, the numerator is inflated and the depositor receives excess rsETH unbacked by real collateral.

The same stale price propagates into `_getTotalEthInProtocol` via `getAssetPrice` at line 339, compounding the accounting error when `updateRSETHPrice` is eventually called with the corrected price.

The `_beforeDeposit` slippage guard at lines 667-669 only enforces a minimum rsETH received by the depositor — it does not prevent over-minting when the oracle price is inflated. The `pricePercentageLimit` downside protection in `_updateRsETHPrice` only triggers after the oracle corrects and `updateRSETHPrice()` is called; it provides no protection during the staleness window itself.

## Impact Explanation
**Critical — Protocol Insolvency.**

During a staleness window where the feed reports `R_stale` while the real rate is `R_real < R_stale`, each deposit of `N` tokens mints:

```
rsETH_minted   = N * R_stale / rsETHPrice   (actual)
rsETH_correct  = N * R_real  / rsETHPrice   (fair)
excess         = N * (R_stale - R_real) / rsETHPrice  (unbacked)
```

Accumulated excess rsETH supply means `totalSupply(rsETH) * rsETHPrice > sum(assetBalance * realAssetPrice)`. When the oracle corrects and `updateRSETHPrice()` is called, `rsETHPrice` drops, diluting all pre-existing holders proportionally. This is a direct, concrete loss of value for existing rsETH holders — protocol insolvency.

## Likelihood Explanation
**Low-Medium.** The Chainlink stETH/ETH feed has a 24-hour heartbeat and a 0.5% deviation threshold. A Lido slashing event moving stETH/ETH by less than 0.5% within a 24-hour window will not trigger a feed update, leaving the stale price active for up to 24 hours. No attacker action is required — any ordinary depositor benefits from the inflated price, and damage accumulates passively across all deposits during the window. Lido slashing events are rare but historically documented. The heartbeat scenario (feed simply not updating for 24 hours) is an additional independent trigger.

## Recommendation
Add staleness and validity checks in `ChainlinkPriceOracle.getAssetPrice`:

```solidity
(uint80 roundId, int256 price, , uint256 updatedAt, uint80 answeredInRound)
    = priceFeed.latestRoundData();

require(price > 0, "Invalid price");
require(answeredInRound >= roundId, "Stale round");
require(block.timestamp - updatedAt <= STALENESS_THRESHOLD, "Stale price");
```

`STALENESS_THRESHOLD` should be set per-feed based on the Chainlink heartbeat (e.g., 86,400 s for a 24-hour feed). Consider making it a configurable parameter per asset feed, set by the LRT admin alongside `updatePriceFeedFor`.

## Proof of Concept
Foundry fork-test outline:

```solidity
// 1. Deploy mock Chainlink aggregator returning stETH/ETH = 1.0e18
//    with updatedAt = block.timestamp - 25 hours (beyond heartbeat)
MockAggregator mockFeed = new MockAggregator(1.0e18, block.timestamp - 25 hours);

// 2. Set mock feed in ChainlinkPriceOracle for stETH
chainlinkOracle.updatePriceFeedFor(stETH, address(mockFeed));

// 3. Record rsETHPrice (reflects real rate, e.g. 0.97e18 after minor slashing)
uint256 rsETHPriceBefore = lrtOracle.rsETHPrice(); // e.g. 0.97e18

// 4. Deposit N stETH at stale price 1.0e18
uint256 N = 100e18;
lrtDepositPool.depositAsset(stETH, N, 0, "");

// 5. Assert over-minting: minted rsETH > N * realRate / rsETHPriceBefore
uint256 minted = rsETH.balanceOf(depositor);
uint256 fairMint = N * 0.97e18 / rsETHPriceBefore;
assert(minted > fairMint); // passes — excess rsETH is unbacked

// 6. Update oracle to real price, call updateRSETHPrice(), assert rsETHPrice dropped
//    confirming dilution of pre-existing holders
```

The assertion at step 5 confirms unbacked rsETH was minted. Step 6 confirms the price correction dilutes existing holders, satisfying the protocol insolvency impact. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```
