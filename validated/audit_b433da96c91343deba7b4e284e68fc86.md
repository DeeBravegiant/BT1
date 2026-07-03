### Title
Stale Chainlink Price Data Used Without Staleness Validation — (`contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards all staleness-related return values (`updatedAt`, `answeredInRound`). A stale LST price is then propagated into rsETH minting calculations, allowing depositors to receive more rsETH than they are entitled to at the expense of existing holders.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

The five return values of `latestRoundData()` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. The code binds only `answer` (`price`) and discards `updatedAt` and `answeredInRound` entirely. There is no check of the form:

- `answeredInRound >= roundId` (round completeness)
- `block.timestamp - updatedAt <= heartbeat` (price freshness)

By contrast, the sibling contract `ChainlinkOracleForRSETHPoolCollateral` does perform a round-completeness check:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
``` [2](#0-1) 

`ChainlinkPriceOracle` has no equivalent guard.

The stale price returned by `ChainlinkPriceOracle.getAssetPrice()` flows directly into two critical paths:

**Path 1 — rsETH price update:**
`LRTOracle._getTotalEthInProtocol()` calls `getAssetPrice(asset)` for every supported LST and sums the ETH-denominated value. This total is then used to compute `newRsETHPrice`. [3](#0-2) 

**Path 2 — rsETH minting:**
`LRTDepositPool.getRsETHAmountToMint()` calls `lrtOracle.getAssetPrice(asset)` to determine how many rsETH tokens to mint for a depositor.

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [4](#0-3) 

---

### Impact Explanation

If a supported LST (e.g., stETH, ETH) experiences a real price drop while the Chainlink feed is stale (still reporting the old, higher price), a depositor calling `depositAsset()` will receive more rsETH than the deposited collateral is actually worth. This dilutes the rsETH/ETH exchange rate for all existing holders — a direct theft of yield/value from current rsETH holders. The severity maps to **Medium: theft of unclaimed yield / permanent freezing of unclaimed yield** depending on the magnitude of the price deviation.

---

### Likelihood Explanation

Chainlink feeds can become stale during:
- Network congestion (L1 gas spikes preventing oracle updates)
- Chainlink node outages
- Rapid market moves that temporarily outpace the deviation threshold

The affected `ChainlinkPriceOracle` is the primary price oracle for all LSTs in the protocol. Any staleness window is exploitable by any unprivileged depositor who monitors on-chain oracle state.

---

### Recommendation

Add staleness validation in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

require(answeredInRound >= roundId, "Stale price: round not complete");
require(updatedAt != 0, "Stale price: incomplete round");
require(block.timestamp - updatedAt <= MAX_STALENESS, "Stale price: price too old");
require(price > 0, "Invalid price");
```

`MAX_STALENESS` should be set per-feed based on the Chainlink heartbeat (e.g., 3600 seconds for ETH/USD, 86400 seconds for some LST feeds).

---

### Proof of Concept

1. Chainlink's stETH/ETH feed becomes stale (e.g., last updated 4 hours ago at 1.05 ETH/stETH).
2. The real stETH price drops to 0.98 ETH/stETH due to a slashing event.
3. An attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
4. `getRsETHAmountToMint()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale `1.05e18`.
5. Attacker receives `1000 * 1.05e18 / rsETHPrice` rsETH — approximately 7% more than fair value.
6. Existing rsETH holders' redemption value is diluted by the over-minted rsETH. [5](#0-4) [6](#0-5)

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

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
