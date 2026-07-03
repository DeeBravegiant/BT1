### Title
Chainlink Oracle Missing Liveness Checks Allows Stale Price Usage for rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards all liveness fields (`roundId`, `updatedAt`, `answeredInRound`). No staleness or round-completeness check is performed. A stale Chainlink price for any supported LST asset flows directly into rsETH minting and redemption calculations, enabling users to mint rsETH at an incorrect rate at the expense of existing holders.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values of `latestRoundData()` are available, but only `price` is used. The fields `roundId`, `updatedAt`, and `answeredInRound` are discarded with no validation. There is no check of the form `block.timestamp > updatedAt + heartbeat` (time-based staleness) nor `answeredInRound < roundId` (round-completeness).

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` — the oracle wrapper used for pool collateral in the same repository — does perform partial liveness checks:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
```

`ChainlinkPriceOracle` is registered as the `assetPriceOracle` for supported LST assets (stETH, rETH, etc.) in `LRTOracle`. Its output feeds directly into `LRTOracle._getTotalEthInProtocol()`, which sums `assetER * totalAssetAmt` for every supported asset to compute the total ETH backing rsETH. This value is then used in `_updateRsETHPrice()` to set `rsETHPrice`, and `rsETHPrice` is used at deposit time to determine how many rsETH tokens to mint per unit of deposited asset.

### Impact Explanation
If a Chainlink feed for any supported LST asset becomes stale (e.g., during a Chainlink node outage, sequencer downtime, or market disruption), `ChainlinkPriceOracle.getAssetPrice()` will return the last recorded price without any revert or warning. If the stale price is higher than the true market price, a depositor can deposit the LST and receive more rsETH than the actual ETH value of their deposit. When `updateRSETHPrice()` is next called with correct prices, the rsETH price decreases, diluting all existing holders. This constitutes theft of yield from existing rsETH holders. If the stale price is significantly inflated, the impact escalates toward protocol insolvency.

**Impact: High — Theft of unclaimed yield / potential protocol insolvency.**

### Likelihood Explanation
Chainlink feeds can go stale during network congestion, sequencer downtime (on L2s), or oracle node failures. The LRT-rsETH protocol supports multiple LST assets, each with its own Chainlink feed, increasing the attack surface. An attacker monitoring mempool or Chainlink heartbeat windows can time a deposit to exploit a stale feed. Likelihood is medium given that Chainlink outages are rare but historically documented.

### Recommendation
Add time-based staleness and round-completeness checks to `ChainlinkPriceOracle.getAssetPrice()`, consistent with the checks already present in `ChainlinkOracleForRSETHPoolCollateral.getRate()`. A configurable `stalePriceDelay` per asset (similar to the referenced report's fix) should be introduced:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
if (block.timestamp > updatedAt + stalePriceDelay[asset]) revert StalePrice();
```

### Proof of Concept
1. Chainlink's ETH/stETH (or any supported LST) feed goes stale — `updatedAt` is 2 hours old, but the true price has dropped 5%.
2. `ChainlinkPriceOracle.getAssetPrice(stETH)` returns the stale (inflated) price with no revert.
3. Attacker calls `LRTDepositPool.depositAsset(stETH, amount)`.
4. `LRTOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` returns inflated price.
5. `_getTotalEthInProtocol()` overstates total ETH, so attacker receives more rsETH than the true ETH value of their deposit.
6. When `updateRSETHPrice()` is called with the correct price, `rsETHPrice` drops, diluting all existing holders.

**Root cause:** [1](#0-0) 

**Contrast with the liveness-checked pool oracle:** [2](#0-1) 

**Stale price flows into total ETH calculation here:** [3](#0-2) 

**Which drives rsETH price and minting:** [4](#0-3)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-32)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);

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
