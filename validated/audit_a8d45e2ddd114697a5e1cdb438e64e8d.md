### Title
Stale Chainlink Price Accepted Without Staleness Validation Enables Over-Minting of rsETH - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` reads `latestRoundData()` but discards all staleness-related return values (`updatedAt`, `answeredInRound`, `roundId`). This is the oracle used by `LRTOracle.getAssetPrice()`, which feeds directly into `LRTDepositPool.getRsETHAmountToMint()`. When a Chainlink feed goes stale at a price higher than the current market price, any depositor can mint more rsETH than they are entitled to, diluting existing holders.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but only captures the `price` field:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No check is made on `updatedAt` (timestamp of last update), `answeredInRound` (must be ≥ `roundId` to confirm the round is complete), or `roundId`. A stale or incomplete round is silently accepted. [1](#0-0) 

The same codebase contains `ChainlinkOracleForRSETHPoolCollateral`, which correctly validates all three conditions before returning a price:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

This inconsistency confirms the developers are aware of the staleness requirement but omitted it from `ChainlinkPriceOracle`.

The stale price propagates through the following call chain:

1. `LRTOracle.getAssetPrice(asset)` → `IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice()`
2. `LRTDepositPool.getRsETHAmountToMint(asset, amount)` → `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()` [3](#0-2) [4](#0-3) 

---

### Impact Explanation

**High — Theft of unclaimed yield / share dilution.**

When a Chainlink feed goes stale at a price above the current market price (e.g., an LST price drops but the feed has not updated within its heartbeat window), the numerator of the rsETH minting formula is inflated. A depositor receives more rsETH shares than the deposited assets are worth. Because rsETH is a share token backed by a fixed pool of ETH-denominated assets, over-minting directly dilutes the redemption value for all existing rsETH holders — equivalent to theft of their accrued yield.

---

### Likelihood Explanation

**Medium.** Chainlink feeds have documented heartbeat intervals (e.g., 24 hours for LST/ETH feeds on mainnet). During periods of high network congestion or rapid price movement, feeds can lag significantly within that window. The `LRTDepositPool.depositAsset()` entry point is fully permissionless — any external caller can exploit the stale price the moment it diverges from market. No privileged role is required.

---

### Recommendation

Add staleness and round-completeness checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    require(answeredInRound >= roundId, "Stale price");
    require(updatedAt != 0, "Incomplete round");
    require(price > 0, "Invalid price");

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, consider enforcing a maximum age threshold (e.g., `block.timestamp - updatedAt <= MAX_STALENESS`) appropriate to each feed's heartbeat.

---

### Proof of Concept

1. The stETH/ETH Chainlink feed has a 24-hour heartbeat. During a market stress event, the feed stalls at `1.05e18` (1.05 ETH per stETH) while the actual market price falls to `1.00e18`.
2. The attacker calls `LRTDepositPool.depositAsset(stETH, 100e18, referralId)`.
3. `getRsETHAmountToMint(stETH, 100e18)` computes:
   - `assetPrice = ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `1.05e18`
   - `rsethAmountToMint = (100e18 * 1.05e18) / rsETHPrice` → 5% more rsETH than deserved
4. The attacker holds rsETH representing 105 ETH of claims against a pool that only received 100 ETH of real value.
5. All existing rsETH holders' redemption value is diluted by the 5 ETH discrepancy. [1](#0-0) [5](#0-4)

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
