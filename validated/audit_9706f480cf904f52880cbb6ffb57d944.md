### Title
Missing Staleness Check in `ChainlinkPriceOracle.getAssetPrice()` Allows Minting rsETH at Stale Inflated Price — (`contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all staleness-related return values (`updatedAt`, `answeredInRound`). When a supported LST's Chainlink feed becomes stale while the asset's market price has dropped, an attacker can deposit the devalued LST and receive rsETH calculated at the old inflated price, directly stealing value from existing rsETH holders.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

The `updatedAt` timestamp and `answeredInRound` are silently ignored. There is no check of the form `require(block.timestamp - updatedAt < MAX_AGE, "stale")` or `require(answeredInRound >= roundId, "stale")`.

This oracle is the price source registered in `LRTOracle.assetPriceOracle` for each supported LST. It is consumed in two critical paths:

**Path 1 — rsETH minting:**
`LRTDepositPool.depositAsset()` → `_beforeDeposit()` → `getRsETHAmountToMint()`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [2](#0-1) 

**Path 2 — rsETH price update:**
`LRTOracle._updateRsETHPrice()` → `_getTotalEthInProtocol()`:

```solidity
uint256 assetER = getAssetPrice(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [3](#0-2) 

`LRTOracle.getAssetPrice()` delegates directly to the registered `IPriceFetcher`:

```solidity
return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
``` [4](#0-3) 

The protocol itself recognises the need for staleness validation — `ChainlinkOracleForRSETHPoolCollateral` (used for pool collateral tokens) does check `answeredInRound < roundID` and reverts with `StalePrice()`:

```solidity
if (answeredInRound < roundID) revert StalePrice();
``` [5](#0-4) 

`ChainlinkPriceOracle`, which guards the core L1 deposit and rsETH price calculation, has no equivalent protection.

---

### Impact Explanation

**Impact: Critical — Direct theft of user funds.**

When a supported LST depegs or its market price drops while the Chainlink feed lags (stale), an attacker can:

1. Deposit the devalued LST into `LRTDepositPool.depositAsset()`.
2. `getRsETHAmountToMint()` uses the stale inflated `getAssetPrice(asset)` in the numerator while the denominator `rsETHPrice` reflects the true value of all other assets.
3. The attacker receives more rsETH than the actual ETH value of the deposited LST warrants.
4. The excess rsETH represents a direct dilution of all existing rsETH holders — their proportional claim on the protocol's underlying assets is reduced.

The magnitude of the loss scales with the size of the depeg and the deposit limit for the affected asset.

---

### Likelihood Explanation

**Likelihood: Medium.**

Chainlink feeds for LSTs (stETH, rETH, cbETH, etc.) have heartbeat intervals of 1–24 hours and deviation thresholds of 0.5–1%. During periods of network congestion or oracle keeper downtime, feeds can lag. LST depeg events (stETH traded at ~0.94 ETH in June 2022) are historically documented. The combination — a feed that has not yet updated after a price drop — is a realistic, non-hypothetical scenario. No privileged access is required; any externally owned account can call `depositAsset()`.

---

### Recommendation

Add a configurable maximum price age and validate both `updatedAt` and `answeredInRound` in `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(answeredInRound >= roundId, "Stale price: answeredInRound");
require(block.timestamp - updatedAt <= maxPriceAge, "Stale price: age exceeded");
require(price > 0, "Invalid price");
```

`maxPriceAge` should be set per-asset based on the Chainlink feed's documented heartbeat interval.

---

### Proof of Concept

1. Protocol supports stETH with `ChainlinkPriceOracle` pointing to the stETH/ETH Chainlink feed.
2. stETH market price drops to 0.94 ETH; the Chainlink feed has not yet updated (still reports 1.00 ETH).
3. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
4. `getRsETHAmountToMint()` computes: `rsethAmountToMint = (1000e18 * 1.00e18) / rsETHPrice`.
5. Attacker receives rsETH worth 1000 ETH, but only deposited assets worth 940 ETH.
6. The 60 ETH difference is extracted from existing rsETH holders' proportional share of the protocol's TVL.
7. When the Chainlink feed updates to 0.94 ETH, `_updateRsETHPrice()` recalculates a lower rsETH price, confirming the dilution. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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

**File:** contracts/LRTOracle.sol (L157-157)
```text
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
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

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-30)
```text
        if (answeredInRound < roundID) revert StalePrice();
```
