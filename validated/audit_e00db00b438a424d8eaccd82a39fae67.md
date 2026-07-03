Audit Report

## Title
Missing Chainlink Price Feed Staleness Check Allows Stale Prices to Corrupt rsETH Minting and TVL Accounting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards `updatedAt` and `answeredInRound`, performing no staleness validation. A stale price is consumed as fresh, directly corrupting the rsETH mint rate in `LRTDepositPool.getRsETHAmountToMint()` and the protocol TVL in `LRTOracle._getTotalEthInProtocol()`. The same codebase already implements the correct staleness pattern in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, confirming this is a known gap.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` reads the Chainlink feed as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All five return values `(roundId, answer, startedAt, updatedAt, answeredInRound)` are available but only `answer` is bound. There is no check that `updatedAt != 0`, no check that `answeredInRound >= roundId`, and no heartbeat window check on `block.timestamp - updatedAt`.

In contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` correctly validates:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

`LRTOracle.getAssetPrice()` delegates directly to `ChainlinkPriceOracle.getAssetPrice()`: [3](#0-2) 

That result flows into two critical paths:

1. **`LRTDepositPool.getRsETHAmountToMint()`** — `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()`: [4](#0-3) 

2. **`LRTOracle._getTotalEthInProtocol()`** — iterates all supported assets and multiplies each balance by `getAssetPrice(asset)` to compute `totalETHInProtocol`, which drives the rsETH price update and fee minting: [5](#0-4) 

## Impact Explanation
When a Chainlink LST/ETH feed is stale with a frozen price above the true current price, any caller of `depositAsset()` or `depositETH()` receives more rsETH than the deposited value warrants. The excess rsETH is backed by the existing pool's assets, directly diluting the yield accrued by all existing rsETH holders. This constitutes **High — Theft of unclaimed yield**. The `minRSETHAmountExpected` slippage guard in `_beforeDeposit()` protects only the depositor from receiving too little; it provides no protection to existing holders against over-issuance. [6](#0-5) 

Additionally, a stale inflated price in `_updateRsETHPrice()` inflates `totalETHInProtocol`, which can suppress the downside-protection pause that should trigger on a real price drop, leaving the protocol exposed. [7](#0-6) 

## Likelihood Explanation
Chainlink feeds on Ethereum mainnet and L2s have documented heartbeat windows (1 hour for ETH/USD, up to 24 hours for some LST feeds). During low-volatility periods a feed may not update for its full heartbeat; during L2 sequencer outages feeds can go stale for hours. No special privileges are required — `depositAsset()` and `depositETH()` are open to any external caller. Any depositor active during a stale window can exploit the mispriced rate. The condition is realistic and repeatable.

## Recommendation
Add staleness and validity checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(answeredInRound >= roundId, "Stale price");
require(updatedAt != 0, "Incomplete round");
require(price > 0, "Invalid price");
require(block.timestamp - updatedAt <= MAX_STALENESS, "Price too old");

return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

`MAX_STALENESS` should be set per feed based on its documented heartbeat (e.g., 3600 seconds for a 1-hour heartbeat feed). Consider storing it alongside `assetPriceFeed` in the mapping.

## Proof of Concept
1. A Chainlink stETH/ETH feed stops updating (e.g., L2 sequencer downtime). Its last reported price is `1.05e18`; the true current price has dropped to `0.99e18`.
2. An attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
3. `_beforeDeposit` → `getRsETHAmountToMint` → `LRTOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `1.05e18`.
4. `rsethAmountToMint = (1000e18 × 1.05e18) / rsETHPrice` — ~6% more rsETH than the deposited value justifies.
5. The attacker holds rsETH backed by only `0.99e18` per stETH, diluting all existing holders by the difference.

**Foundry fork test plan**: Fork mainnet at a block where a target LST/ETH feed has not updated within its heartbeat. Deploy a mock `AggregatorV3Interface` returning a stale `updatedAt` timestamp and an inflated price. Wire it into `ChainlinkPriceOracle` via `updatePriceFeedFor`. Call `depositAsset` as an unprivileged address. Assert that `rsethAmountToMint` exceeds the fair value computed using the true current price, and that existing rsETH holders' redemption value per token has decreased.

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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L666-669)
```text

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
