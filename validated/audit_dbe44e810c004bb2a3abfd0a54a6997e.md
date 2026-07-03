### Title
Stale/Invalid Chainlink Price Accepted Without Validation Causes Incorrect rsETH Price Computation - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validity fields (`updatedAt`, `answeredInRound`, `roundId`). A stale, zero, or negative Chainlink answer is silently accepted and propagated into `LRTOracle._getTotalEthInProtocol()`, corrupting the rsETH/ETH exchange rate used for every deposit and withdrawal.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52-54
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Three critical checks are absent:
1. **Staleness**: `answeredInRound < roundId` is not checked — a round that was never completed returns the last known answer indefinitely.
2. **Incomplete round**: `updatedAt == 0` is not checked — an in-progress round returns `answer = 0`.
3. **Non-positive price**: `price <= 0` is not checked — a negative `int256` cast to `uint256` in Solidity 0.8 wraps to `2^256 - 1`, massively inflating the reported asset value.

The same codebase already implements all three checks correctly in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L27-32
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The unvalidated price flows directly into `LRTOracle._getTotalEthInProtocol()`:

```solidity
// contracts/LRTOracle.sol L339-343
uint256 assetER = getAssetPrice(asset);          // ← stale/invalid value
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

`totalETHInProtocol` then drives `_updateRsETHPrice()`:

```solidity
// contracts/LRTOracle.sol L250
uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

### Impact Explanation
A corrupted `rsETHPrice` directly controls how many rsETH tokens a depositor receives (`LRTDepositPool` uses this rate for minting). An inflated price causes depositors to receive fewer rsETH tokens than owed (fund loss). A deflated or zero price causes depositors to receive far more rsETH than owed (fund theft from the protocol). Additionally, the fee-minting logic in `_updateRsETHPrice()` uses the same corrupted price, enabling incorrect protocol fee extraction. This constitutes share/asset mis-accounting with direct fund impact.

**Impact: High** — Theft of user funds or permanent loss of depositor value depending on direction of price corruption.

### Likelihood Explanation
Chainlink feeds go stale during L2 sequencer outages, network congestion, or when a feed is deprecated. The `answeredInRound < roundId` condition is a documented Chainlink staleness indicator. The `updatedAt == 0` case occurs during an in-progress round. These are not theoretical — they are documented Chainlink edge cases that have occurred on mainnet. Any public caller can trigger `updateRSETHPrice()` at any time, including during a stale-feed window.

**Likelihood: Medium** — Requires a Chainlink feed anomaly, which is an external dependency failure, but the contract provides zero defense against it.

### Recommendation
Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();
require(answeredInRound >= roundId, "Stale price");
require(updatedAt != 0, "Incomplete round");
require(price > 0, "Invalid price");
```

Also apply equivalent validation in `RSETHPriceFeed.latestRoundData()` and `RSETHPriceFeed.getRoundData()`, which forward the raw Chainlink ETH/USD answer without any staleness check.

### Proof of Concept

1. Chainlink's ETH/stETH feed enters a stale round (e.g., L2 sequencer goes down). `latestRoundData()` returns the last known `answer` with `answeredInRound < roundId`.
2. Anyone calls `LRTOracle.updateRSETHPrice()` (public, no access control).
3. `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `LRTOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)`.
4. The stale price (e.g., from 2 hours ago when stETH was worth significantly more or less) is returned without revert.
5. `newRsETHPrice` is computed from the corrupted TVL.
6. A depositor calling `LRTDepositPool.depositAsset()` immediately after receives rsETH minted at the wrong rate — either stealing value from the pool or losing their own. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** contracts/LRTOracle.sol (L249-251)
```text
        // compute new rsETH price based on total ETH minus fee
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

**File:** contracts/oracles/RSETHPriceFeed.sol (L63-70)
```text
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```
