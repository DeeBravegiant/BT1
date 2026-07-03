### Title
Stale Chainlink Price Accepted in `ChainlinkPriceOracle.getAssetPrice()` Enables Over-Minting of rsETH at Expense of Existing Holders - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards the `updatedAt` timestamp and `answeredInRound` fields, accepting any price — including arbitrarily stale ones — without validation. A stale, inflated LST/ETH price fed into the deposit minting formula allows an attacker to receive more rsETH than the actual ETH value of their deposit, diluting all existing rsETH holders.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol, line 52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The return values `updatedAt` (staleness timestamp) and `answeredInRound` (round completeness indicator) are both discarded. No heartbeat check, no `answeredInRound < roundId` check, and no zero-timestamp guard are applied.

This is in direct contrast to `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which is the project's own wrapper for the same `AggregatorV3Interface` and correctly validates all three conditions:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol, lines 27-32
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();

if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The stale price returned by `ChainlinkPriceOracle.getAssetPrice()` flows directly into the rsETH minting formula in `LRTDepositPool.getRsETHAmountToMint()`:

```solidity
// contracts/LRTDepositPool.sol, line 520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

`lrtOracle.getAssetPrice(asset)` delegates to `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)` (i.e., `ChainlinkPriceOracle`), and `rsETHPrice` is itself a stored value updated periodically by `updateRSETHPrice()`. If the Chainlink feed for a supported LST (e.g., stETH, cbETH, rETH) is stale and reports an inflated price, the numerator of the minting formula is artificially elevated, minting excess rsETH to the depositor.

---

### Impact Explanation

**Critical — Direct theft of user funds.**

When an attacker deposits an LST at a stale, inflated oracle price, they receive more rsETH than the actual ETH value of their deposit. The excess rsETH represents a claim on ETH that was contributed by other depositors. When `updateRSETHPrice()` is subsequently called and recomputes the true TVL, the rsETH price drops to reflect the dilution, and all pre-existing rsETH holders lose proportional value. The attacker can then redeem their rsETH for more ETH than they deposited, extracting value directly from other users.

---

### Likelihood Explanation

**Medium.** Chainlink feeds can become stale during:
- Network congestion preventing oracle keeper transactions from landing
- Oracle node downtime or misconfiguration
- A rapid depeg event where the LST loses ETH value faster than the feed updates (the feed lags behind, still reporting the pre-depeg price)

The attack is most profitable during a depeg: the attacker deposits the depegged LST at the still-inflated oracle price, receiving rsETH priced against the pre-depeg rate, then redeems for ETH once the oracle catches up. No privileged access is required; `depositAsset()` is open to any caller.

---

### Recommendation

Add staleness and completeness checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(answeredInRound >= roundId, "Stale price");
require(updatedAt != 0, "Incomplete round");
require(price > 0, "Invalid price");
require(block.timestamp - updatedAt <= MAX_STALENESS, "Price too old");
```

`MAX_STALENESS` should be set per-feed based on the Chainlink heartbeat for that asset pair (e.g., 3600 seconds for most ETH-denominated LST feeds).

---

### Proof of Concept

1. Chainlink's stETH/ETH feed becomes stale (e.g., last updated 2 hours ago at 1.05 ETH/stETH; actual market rate has dropped to 0.97 ETH/stETH due to a depeg).
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
3. `_beforeDeposit` → `getRsETHAmountToMint(stETH, 1000e18)` → `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` returns `1.05e18` (stale).
4. Suppose `rsETHPrice = 1.02e18`. Minted rsETH = `(1000e18 * 1.05e18) / 1.02e18 ≈ 1029.4 rsETH`.
5. Actual ETH value deposited = `1000 * 0.97 = 970 ETH`. Fair rsETH at 1.02 = `970 / 1.02 ≈ 950.98 rsETH`.
6. Attacker received `≈ 78.4 rsETH` in excess — a claim on `≈ 79.97 ETH` contributed by other depositors.
7. When `updateRSETHPrice()` is called, the true TVL is lower than expected, rsETH price drops, and all prior holders are diluted by the attacker's excess claim.

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
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
