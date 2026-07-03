### Title
Stale Chainlink Price Feed Used Without Validation Enables Excess rsETH Minting — (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validity fields (`updatedAt`, `answeredInRound`, `roundId`) and does not check for a non-positive price. A stale or zero price from a Chainlink feed is silently accepted and propagated into rsETH minting calculations, allowing a depositor to receive excess rsETH when an LST has depegged but its feed has not updated.

---

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values of `latestRoundData()` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. The code discards `roundId`, `startedAt`, `updatedAt`, and `answeredInRound`, performing no checks:

- No staleness check (`block.timestamp - updatedAt > heartbeat`)
- No incomplete-round check (`answeredInRound >= roundId`)
- No non-positive price check (`price > 0`)

This is in direct contrast to `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which is the protocol's own wrapper for the same Chainlink interface and explicitly validates all three conditions before returning a price.

The stale price flows into two critical paths:

**Path 1 — rsETH minting at deposit:**
`LRTDepositPool.getRsETHAmountToMint()` computes:
```
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()
```
`lrtOracle.getAssetPrice(asset)` delegates directly to `ChainlinkPriceOracle.getAssetPrice()`. An inflated stale price inflates the numerator, minting excess rsETH for the depositor.

**Path 2 — rsETH price update:**
`LRTOracle._getTotalEthInProtocol()` iterates all supported assets and calls `getAssetPrice(asset)` for each, summing `totalAssetAmt * assetER`. A stale inflated price inflates `totalETHInProtocol`, which then inflates `newRsETHPrice = totalETHInProtocol / rsethSupply`, causing the oracle to record a falsely elevated rsETH price and potentially minting excess protocol fee rsETH to the treasury.

---

### Impact Explanation
**Impact: High — Theft of unclaimed yield / share dilution of existing rsETH holders.**

When a supported LST depegs (e.g., stETH or similar) but the Chainlink feed is stale (last update reflects the pre-depeg price), a depositor can:
1. Deposit the devalued LST at the stale (inflated) oracle price.
2. Receive more rsETH than the LST is actually worth.
3. Redeem or sell the excess rsETH, extracting value from existing rsETH holders whose share of the backing TVL is diluted.

The magnitude scales with the size of the depeg and the deposit limit. Even a 1–2% stale price discrepancy on a large deposit extracts meaningful yield from the pool.

---

### Likelihood Explanation
**Likelihood: Medium.**

Chainlink feeds go stale in documented, recurring scenarios:
- L2 sequencer downtime (Arbitrum, Optimism) — feeds stop updating while the sequencer is offline.
- Network congestion causing Chainlink keeper transactions to fail.
- Feed heartbeat windows (e.g., 24 hours for some LST/ETH feeds) mean a price can be up to 24 hours old before any on-chain staleness is detectable.

LST depegs (even temporary ones) are also a known occurrence in the ecosystem. The combination of a stale feed and a depeg event is a realistic, non-hypothetical scenario.

---

### Recommendation
Add the same validation checks already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price, , uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optional: add a configurable heartbeat staleness check
    // if (block.timestamp - updatedAt > STALENESS_THRESHOLD) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, configure a per-feed maximum staleness threshold (heartbeat + buffer) and revert if `block.timestamp - updatedAt` exceeds it.

---

### Proof of Concept

1. Chainlink's stETH/ETH feed last updated at `T-25h` with price `1.0 ETH` (within its 24h heartbeat, so no on-chain circuit breaker fires). stETH has since depegged to `0.97 ETH` on-market.

2. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.

3. `_beforeDeposit` calls `getRsETHAmountToMint(stETH, 1000e18)`:
   ```
   rsethAmountToMint = (1000e18 * getAssetPrice(stETH)) / rsETHPrice
                     = (1000e18 * 1.0e18) / 1.0e18   ← stale price used
                     = 1000 rsETH
   ```
   Correct amount at actual price would be `970 rsETH`.

4. Attacker receives `1000 rsETH` for `1000 stETH` worth only `970 ETH`, extracting `30 rsETH` (~3%) of value from existing holders.

5. No revert occurs because `ChainlinkPriceOracle.getAssetPrice()` performs zero validation on the returned price data. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L648-669)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
