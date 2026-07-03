### Title
Unvalidated Chainlink `latestRoundData()` Return in `ChainlinkPriceOracle` Allows Zero/Stale Price to Corrupt rsETH Price and Enable Fund Theft - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validity fields, silently accepting a zero or stale price. This zero propagates into `LRTOracle._getTotalEthInProtocol()`, deflating the computed TVL and causing `rsETHPrice` to be written to an artificially low value. Any subsequent depositor then receives excess rsETH at the deflated rate, directly diluting existing rsETH holders.

---

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` reads only the `answer` field from `latestRoundData()` and performs no validation:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol:52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

Three critical checks are absent:
- `answeredInRound >= roundId` — detects an incomplete/stale round where `price` is 0
- `updatedAt != 0` — detects an incomplete round
- `price > 0` — rejects a zero or negative answer

The same codebase contains `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which correctly applies all three checks:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol:30-32
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

`ChainlinkPriceOracle` is the oracle wired into `LRTOracle` for L1 LST assets (stETH, cbETH, etc.) via `assetPriceOracle[asset]`. When `LRTOracle._getTotalEthInProtocol()` iterates over supported assets, it calls `getAssetPrice(asset)` for each:

```solidity
// contracts/LRTOracle.sol:339-343
uint256 assetER = getAssetPrice(asset);
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [3](#0-2) 

If `assetER` is 0 (stale Chainlink round), that asset's entire TVL contribution is silently zeroed out. The resulting `totalETHInProtocol` is deflated, and `_updateRsETHPrice()` writes a lower-than-actual `rsETHPrice` to storage:

```solidity
// contracts/LRTOracle.sol:250,313
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
...
rsETHPrice = newRsETHPrice;
``` [4](#0-3) [5](#0-4) 

`LRTDepositPool.getRsETHAmountToMint()` then uses this deflated `rsETHPrice` to compute how much rsETH to mint per deposited asset:

```solidity
// contracts/LRTDepositPool.sol:520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [6](#0-5) 

A lower `rsETHPrice` denominator means more rsETH is minted per unit of deposited asset, directly diluting all existing rsETH holders.

---

### Impact Explanation

**Critical — Direct theft of user funds.**

Existing rsETH holders' share of the underlying TVL is diluted when new depositors receive excess rsETH minted against the artificially deflated `rsETHPrice`. The attacker's gain is the existing holders' loss, proportional to the magnitude of the price deflation and the deposit size.

---

### Likelihood Explanation

**Medium.** Chainlink feeds enter a state where `answeredInRound < roundId` (and `price = 0`) during oracle downtime, network congestion, or feed deprecation. `updateRSETHPrice()` is a permissionless public function callable by anyone, so an attacker can time the call to coincide with a stale round and immediately follow with a deposit. [7](#0-6) 

---

### Recommendation

Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
``` [8](#0-7) 

---

### Proof of Concept

1. A Chainlink LST/ETH feed (e.g. stETH/ETH) enters a stale round: `answeredInRound < roundId`, causing `latestRoundData()` to return `price = 0`.
2. Attacker calls `LRTOracle.updateRSETHPrice()` (permissionless).
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns `0`.
4. `totalETHInProtocol` is computed with stETH's TVL contribution zeroed out.
5. `newRsETHPrice = deflatedTVL / rsethSupply` — significantly below the true price.
6. If the drop is within `pricePercentageLimit`, `rsETHPrice` is written to the deflated value (no pause triggered).
7. Attacker immediately calls `LRTDepositPool.depositAsset(stETH, largeAmount, 0)`.
8. `getRsETHAmountToMint` computes `(largeAmount * stETHPrice) / deflatedRsETHPrice` → mints excess rsETH.
9. Attacker holds excess rsETH; when the oracle recovers and `rsETHPrice` is corrected upward, the attacker's rsETH is worth more than they paid, at the expense of existing holders.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-36)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
```

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

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
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
