### Title
Missing Chainlink Round Validity Checks in `ChainlinkPriceOracle.getAssetPrice()` Enables rsETH Overminting at Stale Prices - (File: contracts/oracles/ChainlinkPriceOracle.sol)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards every return value except `answer`, performing no staleness, completeness, or sign checks. The same codebase's `ChainlinkOracleForRSETHPoolCollateral.getRate()` performs all three checks (`answeredInRound < roundID`, `timestamp == 0`, `ethPrice <= 0`). Because `ChainlinkPriceOracle` is the oracle wired into `LRTOracle` and therefore into `LRTDepositPool.getRsETHAmountToMint()`, a stale or zero price silently propagates into rsETH minting math, allowing an attacker to mint rsETH at an incorrect exchange rate and extract value from existing holders.

---

### Finding Description

**Root cause — `ChainlinkPriceOracle.getAssetPrice()` (line 52):**

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values of `latestRoundData()` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. The code silently discards `roundId`, `startedAt`, `updatedAt`, and `answeredInRound`. No check is made that:
- `answeredInRound >= roundId` (stale round detection)
- `updatedAt != 0` (incomplete round detection)
- `price > 0` (invalid/negative price detection)

**Contrast with the correct implementation in the same repo — `ChainlinkOracleForRSETHPoolCollateral.getRate()` (lines 27–32):**

```solidity
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();

if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The developers clearly know the correct pattern; it is simply absent from the production oracle used for LST pricing.

**Exploit path:**

1. Chainlink's feed for a supported LST (e.g., stETH/ETH) goes stale — the feed stops updating while the actual market price of stETH drops (e.g., from 1.05 ETH to 0.95 ETH due to a depeg event). The stale `answeredInRound < roundId` condition is now true, but `ChainlinkPriceOracle` does not check it.

2. `rsETHPrice` was last updated (via `LRTOracle.updateRSETHPrice()`) when stETH was at 1.05 ETH, so `rsETHPrice ≈ 1.05 ETH` in storage.

3. An attacker calls `LRTDepositPool.depositAsset(stETH, amount, ...)`. Internally:
   - `getRsETHAmountToMint()` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice()` → returns the stale price of **1.05 ETH** instead of the real 0.95 ETH.
   - `rsethAmountToMint = (amount × 1.05e18) / 1.05e18 = amount` (1:1 ratio).
   - The correct ratio should be `0.95 / 1.05 ≈ 0.905` rsETH per stETH.

4. The attacker receives ~10% more rsETH than their stETH is worth. Once the oracle recovers and `rsETHPrice` is updated to reflect the actual lower TVL, the attacker redeems their excess rsETH for ETH, extracting value from all other rsETH holders.

---

### Impact Explanation

**Severity: Critical — Direct theft of user funds.**

The attacker mints rsETH at an inflated asset price, receiving more rsETH than the deposited assets are worth. Upon redemption after oracle recovery, the attacker withdraws more ETH than they deposited. The loss is borne pro-rata by all existing rsETH holders whose share of the underlying TVL is diluted. The magnitude scales with the size of the deposit and the degree of price staleness; during a significant LST depeg event (which has occurred historically with stETH), the discrepancy can be material.

---

### Likelihood Explanation

Chainlink feeds go stale during network congestion, sequencer downtime, or oracle node failures. LST/ETH feeds have historically experienced update delays. An attacker monitoring on-chain oracle state can detect the stale condition (by reading `answeredInRound` and `roundId` directly from the Chainlink aggregator) and immediately submit a deposit transaction. No privileged access is required; `depositAsset()` and `depositETH()` are fully public entry points.

---

### Recommendation

Apply the same three-field validation already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, consider adding a `block.timestamp - updatedAt > MAX_STALENESS` heartbeat check tuned to each feed's expected update frequency.

---

### Proof of Concept

1. Observe that `ChainlinkPriceOracle.getAssetPrice()` at line 52 discards all `latestRoundData()` fields except `answer`: [1](#0-0) 

2. Observe that `ChainlinkOracleForRSETHPoolCollateral.getRate()` in the same repo performs all three validity checks: [2](#0-1) 

3. `LRTOracle.getAssetPrice()` delegates directly to `ChainlinkPriceOracle.getAssetPrice()` with no additional validation: [3](#0-2) 

4. `LRTOracle._getTotalEthInProtocol()` uses this price to compute total protocol TVL: [4](#0-3) 

5. `LRTDepositPool.getRsETHAmountToMint()` uses both `getAssetPrice()` and `rsETHPrice` to compute how many rsETH tokens to mint for a depositor: [5](#0-4) 

6. `depositAsset()` (publicly callable by any user) uses `getRsETHAmountToMint()` and mints the result directly to the caller: [6](#0-5) 

When the Chainlink feed for a supported LST is stale (detectable via `answeredInRound < roundId` on the aggregator), an attacker deposits that LST and receives rsETH computed at the stale price. If the stale price exceeds the current market price, the attacker receives more rsETH than their deposit is worth, diluting all existing holders upon redemption.

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

**File:** contracts/LRTOracle.sol (L336-344)
```text
        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

```

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L516-521)
```text
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
