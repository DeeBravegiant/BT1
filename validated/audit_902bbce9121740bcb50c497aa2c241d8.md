### Title
Missing Chainlink `latestRoundData` Staleness and Validity Checks Allow Stale/Invalid Prices to Corrupt rsETH Exchange Rate - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validation return values (`roundId`, `updatedAt`, `answeredInRound`). No checks are performed for a stale round, an incomplete round, or a non-positive price. This stale or invalid price propagates directly into the rsETH/ETH exchange rate computation, affecting every depositor and withdrawer.

---

### Finding Description

In `contracts/oracles/ChainlinkPriceOracle.sol`, `getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values of `latestRoundData()` are available — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — but only `answer` is used. The contract performs none of the standard Chainlink safety checks:

- `price > 0` — a zero or negative answer is silently cast to a huge `uint256` (via underflow) or zero.
- `updatedAt != 0` — an incomplete round returns `updatedAt == 0`.
- `answeredInRound >= roundId` — a carried-over answer from a prior round indicates a stale price.

By contrast, `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` in the same repository correctly validates all three conditions:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The call chain from the vulnerable function to the rsETH price is:

1. `ChainlinkPriceOracle.getAssetPrice()` — returns stale/invalid price.
2. `LRTOracle.getAssetPrice()` — delegates to the above.
3. `LRTOracle._getTotalEthInProtocol()` — sums `assetER * totalAssetAmt` for all supported assets.
4. `LRTOracle._updateRsETHPrice()` — computes `newRsETHPrice = totalETHInProtocol / rsethSupply`.
5. `rsETHPrice` is written with the corrupted value.

`updateRSETHPrice()` is a public, permissionless function callable by anyone.

---

### Impact Explanation

A stale or invalid Chainlink price for any supported LST asset (e.g., stETH, cbETH, rETH) causes `_getTotalEthInProtocol()` to return an incorrect TVL. This directly corrupts `rsETHPrice`, the exchange rate used for all deposits and withdrawals:

- **Inflated price**: depositors receive fewer rsETH shares than owed; existing holders are diluted in reverse — effectively a theft of yield/principal from new depositors.
- **Deflated price**: depositors receive more rsETH than owed, draining value from existing holders (theft of unclaimed yield / protocol insolvency path).
- **Zero price** (e.g., `updatedAt == 0` incomplete round): the asset's entire TVL contribution is zeroed, collapsing `rsETHPrice` and triggering the downside-protection pause, temporarily freezing all funds.

Impact: **Temporary freezing of funds / theft of unclaimed yield / protocol insolvency**, depending on the direction and magnitude of the stale price deviation.

---

### Likelihood Explanation

Chainlink feeds do go stale during network congestion, sequencer downtime (on L2s), or feed deprecation. The vulnerability is passively exploitable: any caller invoking the public `updateRSETHPrice()` during a period of feed staleness will corrupt the stored `rsETHPrice`. No special privileges are required.

---

### Recommendation

Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral.sol`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    require(price > 0, "Chainlink price <= 0");
    require(updatedAt != 0, "Incomplete round");
    require(answeredInRound >= roundId, "Stale price");

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Additionally, consider adding a `block.timestamp - updatedAt <= MAX_STALENESS` heartbeat check tuned to each feed's update frequency.

---

### Proof of Concept

**Root cause** — `ChainlinkPriceOracle.getAssetPrice()` discards all validation fields: [1](#0-0) 

**Propagation** — `LRTOracle._getTotalEthInProtocol()` uses the corrupted price to compute total ETH: [2](#0-1) 

**rsETH price written from corrupted TVL** — `_updateRsETHPrice()` sets `rsETHPrice`: [3](#0-2) 

**Public entry point** — anyone can trigger the update: [4](#0-3) 

**Correct pattern already in the same repo** — `ChainlinkOracleForRSETHPoolCollateral` validates all fields: [5](#0-4)

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

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
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

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-33)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

```
