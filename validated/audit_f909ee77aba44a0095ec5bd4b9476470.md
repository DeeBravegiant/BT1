Audit Report

## Title
No Staleness Check on Chainlink `latestRoundData()` in `ChainlinkPriceOracle` Allows Stale Asset Prices to Corrupt rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls Chainlink's `latestRoundData()` but silently discards all validation fields (`roundId`, `updatedAt`, `answeredInRound`), accepting any price the feed returns regardless of freshness. This stale price propagates through `LRTOracle._getTotalEthInProtocol()` into `_updateRsETHPrice()`, corrupting the global `rsETHPrice` exchange rate used for all deposits and withdrawals. The sister contract `ChainlinkOracleForRSETHPoolCollateral` in the same repository already implements the missing guards, confirming the omission is unintentional.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` at [1](#0-0)  fetches the price as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values — `(roundId, answer, startedAt, updatedAt, answeredInRound)` — are available, but only `answer` is used. `updatedAt` and `answeredInRound` are discarded with no staleness check.

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repository explicitly guards against this at [2](#0-1) :

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The stale price returned by `ChainlinkPriceOracle.getAssetPrice()` is consumed by `LRTOracle.getAssetPrice()` at [3](#0-2) , which is called inside `_getTotalEthInProtocol()` at [4](#0-3) , which feeds into `_updateRsETHPrice()` at [5](#0-4) .

`updateRSETHPrice()` is a public, permissionless function callable by any user at any time when the contract is not paused: [6](#0-5) 

The `pricePercentageLimit` downside-protection mechanism at [7](#0-6)  provides partial mitigation only for large deviations (auto-pause), but does not protect against small-to-moderate stale price deviations, and is configurable to 0 (disabled).

## Impact Explanation
**High — Theft of unclaimed yield:** A stale deflated price for any supported LST (e.g., rETH, stETH, cbETH) causes `_getTotalEthInProtocol()` to understate TVL, setting `rsETHPrice` lower than warranted. New depositors then receive more rsETH than they should at this artificially low exchange rate. When the price corrects, the inflated rsETH supply dilutes existing holders' proportional claim on protocol assets — constituting theft of unclaimed yield from existing rsETH holders.

**Low — Contract fails to deliver promised returns:** A stale inflated price overstates TVL, setting `rsETHPrice` higher than warranted. Depositors receive fewer rsETH tokens than they should, failing to deliver the promised exchange rate.

## Likelihood Explanation
Chainlink feeds can go stale during network congestion, sequencer downtime, or feed deprecation. `updateRSETHPrice()` is public and permissionless — any unprivileged external caller can trigger it at any time while a feed is stale, locking in the corrupted exchange rate. No special privileges, front-running, or victim mistakes are required. The condition is repeatable whenever a feed lags.

## Recommendation
Add staleness checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (price <= 0) revert InvalidPrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (answeredInRound < roundId) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Optionally, add a configurable `heartbeat` per feed and enforce `block.timestamp - updatedAt <= heartbeat`.

## Proof of Concept
1. A supported LST asset (e.g., rETH) has its Chainlink feed go stale (last updated 25 hours ago, `answeredInRound < roundId`).
2. Any user calls `LRTOracle.updateRSETHPrice()`.
3. `_getTotalEthInProtocol()` calls `getAssetPrice(rETH)` → `ChainlinkPriceOracle.getAssetPrice(rETH)` → returns the 25-hour-old price with no revert.
4. `_updateRsETHPrice()` computes `newRsETHPrice` using the stale TVL and stores it as `rsETHPrice`.
5. A new depositor deposits at the corrupted (deflated) `rsETHPrice`, receiving excess rsETH, diluting existing holders.
6. All subsequent deposits and withdrawals use the corrupted exchange rate until the next valid price update.

**Foundry fork test plan:** Fork mainnet, mock a Chainlink aggregator to return a stale round (`answeredInRound < roundId`, `updatedAt` = 25 hours ago), call `updateRSETHPrice()` as an unprivileged address, assert `rsETHPrice` deviates from the correct value, and assert a subsequent deposit mints more rsETH than the correct rate would allow.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L231-250)
```text
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
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
