### Title
Missing Chainlink `latestRoundData()` Return Value Validation Allows Stale Price to Corrupt rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards `roundId`, `updatedAt`, and `answeredInRound`. A stale or zero price flows directly into `LRTOracle._getTotalEthInProtocol()`, corrupting the `rsETHPrice` used for all deposits and withdrawals.

### Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol` line 52, the `getAssetPrice()` function calls Chainlink's `latestRoundData()` but only captures the `price` field, discarding all staleness-detection fields:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No checks are performed on:
- `answeredInRound >= roundId` (detects a price carried over from a prior incomplete round)
- `updatedAt != 0` (detects an incomplete round)
- `price > 0` (Chainlink returns 0 when no answer has been reached; casting a negative `int256` to `uint256` produces an astronomically large value)

This is in direct contrast to `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`, which correctly validates all three conditions before returning a price.

The stale/invalid price propagates through the following call chain:

1. `LRTOracle._updateRsETHPrice()` → `_getTotalEthInProtocol()` → `getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice()` → stale `price` returned
2. `_getTotalEthInProtocol()` multiplies the stale price by total deposited asset amounts to compute `totalETHInProtocol`
3. `newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply)` is set from this corrupted TVL
4. `rsETHPrice` is stored and used by `LRTDepositPool` to determine how many rsETH tokens to mint per deposit

### Impact Explanation
**High — Theft of unclaimed yield from existing rsETH holders.**

If a Chainlink feed returns a stale price lower than the true market price (e.g., during oracle downtime or network congestion), `totalETHInProtocol` is underestimated, `rsETHPrice` is set below its true value, and a depositor calling `depositETH()` or depositing an LST receives more rsETH than the assets they contributed are worth. This dilutes the share value of all existing rsETH holders, constituting theft of their accrued yield. Conversely, if `price` is 0 (Chainlink's documented behavior when no answer is reached), `uint256(0)` causes the asset's contribution to TVL to vanish entirely, crashing `rsETHPrice` and potentially triggering the downside-protection pause, temporarily freezing all user funds.

### Likelihood Explanation
Chainlink feeds are known to go stale during periods of low volatility (heartbeat not triggered), network congestion, or sequencer downtime on L2s. The protocol already deploys on multiple chains (Scroll, Base, Arbitrum, Optimism, Linea). The absence of any staleness guard means any such event directly corrupts the exchange rate with no on-chain protection. Likelihood is **medium** — it requires an external oracle condition, but that condition is well-documented and has occurred historically.

### Recommendation
Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral.sol` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price, , uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, consider adding a `block.timestamp - updatedAt > MAX_DELAY` heartbeat check per feed.

### Proof of Concept

1. Chainlink's stETH/ETH feed goes stale (heartbeat not triggered for 24 h during low volatility). The feed returns the last known price from a prior round with `answeredInRound < roundId`.

2. Anyone calls `LRTOracle.updateRSETHPrice()` (public, permissionless): [1](#0-0) 

3. `_updateRsETHPrice()` calls `_getTotalEthInProtocol()`, which iterates supported assets and calls `getAssetPrice(stETH)`: [2](#0-1) 

4. `getAssetPrice` delegates to `ChainlinkPriceOracle.getAssetPrice()`, which calls `latestRoundData()` and discards all staleness fields, returning the stale price unchecked: [3](#0-2) 

5. The stale (artificially low) price reduces `totalETHInProtocol`, causing `newRsETHPrice` to be set below true value: [4](#0-3) 

6. A depositor immediately calls `LRTDepositPool.depositETH()` and receives rsETH minted at the deflated `rsETHPrice`, obtaining more rsETH than their ETH is worth, diluting all existing holders.

7. Compare: `ChainlinkOracleForRSETHPoolCollateral` — used for pool collateral pricing — correctly validates all three conditions: [5](#0-4)

### Citations

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

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-36)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
```
