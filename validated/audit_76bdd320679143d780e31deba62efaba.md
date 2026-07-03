Audit Report

## Title
Missing Chainlink Oracle Staleness Check Allows Stale Prices to Corrupt rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `answer`, applying no staleness, zero-price, or incomplete-round checks. A stale inflated price propagates into `LRTOracle._updateRsETHPrice()`, causing phantom TVL growth that triggers unearned protocol fee minting to the treasury, diluting rsETH holders' unclaimed yield.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` at [1](#0-0)  captures only `price` from `latestRoundData()`, discarding `roundId`, `startedAt`, `updatedAt`, and `answeredInRound`. No staleness guard of any kind is applied.

This price is consumed by `LRTOracle.getAssetPrice()` [2](#0-1) , which is called inside `_getTotalEthInProtocol()` for every supported LST asset. [3](#0-2) 

`_updateRsETHPrice()` then computes `protocolFeeInETH` as the difference between `totalETHInProtocol` and `previousTVL`. If the stale price inflates `totalETHInProtocol` above `previousTVL`, the condition `totalETHInProtocol > previousTVL` is satisfied and protocol fees are minted to the treasury. [4](#0-3) 

The fee is minted as rsETH directly to the treasury address: [5](#0-4) 

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` applies `answeredInRound < roundID`, `timestamp == 0`, and `ethPrice <= 0` guards, confirming the team is aware of the requirement. [6](#0-5)  These checks are entirely absent from `ChainlinkPriceOracle`.

`updateRSETHPrice()` is `public whenNotPaused`, callable by any unprivileged account. [7](#0-6) 

The `pricePercentageLimit` downside-protection guard does not mitigate this: it only triggers a pause when the price *drops* beyond a threshold, and only if `pricePercentageLimit > 0` is configured. A stale *inflated* price bypasses it entirely. [8](#0-7) 

## Impact Explanation
**High — Theft of unclaimed yield.** When a Chainlink LST/ETH feed goes stale with an inflated last price (e.g., during a low-volatility heartbeat window or oracle node outage), any caller invoking `updateRSETHPrice()` causes `_updateRsETHPrice()` to record phantom TVL growth. The protocol mints rsETH to the treasury against yield that does not exist, permanently diluting the share of real accrued yield belonging to existing rsETH holders. The minted fee rsETH is not reversible without admin intervention, and the `highestRsethPrice` is updated to the inflated value, permanently raising the baseline for future fee calculations.

## Likelihood Explanation
Chainlink LST/ETH feeds (e.g., stETH/ETH, rETH/ETH) have heartbeat intervals of up to 24 hours. During low-volatility periods, feeds routinely go the full heartbeat without updating. No special attacker capability is required: any EOA can call `LRTOracle.updateRSETHPrice()` at any time the protocol is unpaused. The condition is passively triggered by normal protocol operation during any stale window.

## Recommendation
Add staleness validation in `ChainlinkPriceOracle.getAssetPrice()` mirroring the pattern already present in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
if (block.timestamp - updatedAt > maxStaleness[asset]) revert PriceOutdated();
```

Add a per-asset `maxStaleness` mapping settable by `onlyLRTManager`, with values matching each feed's documented heartbeat interval.

## Proof of Concept
1. A Chainlink LST/ETH feed (e.g., stETH/ETH) stops updating; `latestRoundData()` continues returning the last cached `price` with a stale `updatedAt`.
2. The stale price is higher than the true current price (e.g., last update was at a local high before a small dip).
3. Any EOA calls `LRTOracle.updateRSETHPrice()`.
4. `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `ChainlinkPriceOracle.getAssetPrice(stETH)` returns the stale inflated price with no revert.
5. `totalETHInProtocol` is overstated; `totalETHInProtocol > previousTVL` evaluates true.
6. `protocolFeeInETH` is computed on phantom yield; rsETH is minted to the treasury.
7. Existing rsETH holders' proportional claim on real protocol yield is permanently diluted.

**Foundry fork test plan**: Fork mainnet, mock a Chainlink stETH/ETH aggregator to return a fixed stale answer with `updatedAt = block.timestamp - 2 days`, call `LRTOracle.updateRSETHPrice()`, assert that `IRSETH(rsETH).balanceOf(treasury)` increased and that `rsETHPrice` reflects the inflated stale value.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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

**File:** contracts/LRTOracle.sol (L244-247)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
```

**File:** contracts/LRTOracle.sol (L270-282)
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
            }
```

**File:** contracts/LRTOracle.sol (L299-308)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
            if (rsethAmountToMintAsProtocolFee > 0) {
                address treasury = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
                IRSETH(rsETHTokenAddress).mint(treasury, rsethAmountToMintAsProtocolFee);
                emit FeeMinted(treasury, rsethAmountToMintAsProtocolFee);
            }
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

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-33)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

```
