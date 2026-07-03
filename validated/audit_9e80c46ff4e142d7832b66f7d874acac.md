### Title
Missing Chainlink `latestRoundData` Return Value Validation Allows Stale/Zero/Negative Prices to Corrupt rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `price`. No checks are performed on `answeredInRound`, `updatedAt`, or the sign/zero status of `price`. A stale, zero, or negative Chainlink answer is silently accepted and propagated into rsETH mint calculations for every depositor.

### Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol`, `getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All five return values of `latestRoundData` — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — are available, but only `answer` (as `price`) is used. The following critical checks are absent:

1. **Stale price**: No `answeredInRound >= roundId` check. Chainlink returns the last known answer even if the oracle has stopped updating.
2. **Incomplete round**: No `updatedAt != 0` check. A round that never completed returns `updatedAt == 0`.
3. **Zero price**: No `price > 0` check. Chainlink documents that `latestRoundData` returns `0` when no answer has been reached.
4. **Negative price**: No `price > 0` guard before the `uint256(price)` cast. In Solidity 0.8, explicit `int256 → uint256` conversion of a negative value does **not** revert — it wraps to a near-`type(uint256).max` value.

By contrast, the sibling contract `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` correctly implements all three guards:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

`ChainlinkPriceOracle` is the oracle registered for LST assets (stETH, cbETH, etc.) in `LRTOracle`. Its output flows directly into rsETH mint calculations.

### Impact Explanation

The corrupted price propagates through two paths:

**Path 1 — Direct deposit minting:**
`LRTDepositPool.getRsETHAmountToMint()` computes:
```
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()
``` [3](#0-2) 

- If `price == 0`: depositor receives 0 rsETH for real assets deposited → **temporary freeze of depositor funds**.
- If `price` is stale-high: depositor receives excess rsETH → **theft of yield/value from existing rsETH holders**.
- If `price` is negative (wraps to ~`2^256`): `rsethAmountToMint` overflows to an astronomically large value → **critical: direct theft / protocol insolvency**.

**Path 2 — rsETH price update:**
`LRTOracle._updateRsETHPrice()` calls `_getTotalEthInProtocol()`, which aggregates `getAssetPrice()` for all supported assets to compute the protocol TVL. A corrupted asset price inflates or deflates `totalETHInProtocol`, directly corrupting `rsETHPrice` stored on-chain, affecting all subsequent deposits and withdrawals. [4](#0-3) 

### Likelihood Explanation
Chainlink feeds do go stale during network congestion, sequencer downtime (on L2s), or when a feed is deprecated. The zero-answer case is explicitly documented by Chainlink. The negative-price case is less common but possible with misconfigured custom aggregators. The entry path (`depositAsset`, `depositETH`, `updateRSETHPrice`) is fully permissionless — any user can trigger it at any time.

### Recommendation
Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Optionally add a heartbeat check: `if (block.timestamp - updatedAt > HEARTBEAT_INTERVAL) revert StalePrice();`

### Proof of Concept

1. Chainlink's ETH/stETH feed goes stale (e.g., sequencer downtime on an L2 deployment, or feed deprecation). The last stored answer is `0`.
2. Anyone calls `LRTDepositPool.depositAsset(stETH, 10 ether, 0)`.
3. `getRsETHAmountToMint` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)`.
4. `latestRoundData()` returns `(roundId=5, answer=0, startedAt=T, updatedAt=0, answeredInRound=4)`. No revert occurs.
5. `uint256(0) * 1e18 / decimals = 0` is returned as the asset price.
6. `rsethAmountToMint = (10e18 * 0) / rsETHPrice = 0`.
7. The depositor's 10 stETH is transferred in, but they receive 0 rsETH — funds are locked with no recourse until the oracle recovers and a new deposit is made. [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** contracts/LRTDepositPool.sol (L515-521)
```text
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

**File:** contracts/LRTOracle.sol (L231-231)
```text
        uint256 totalETHInProtocol = _getTotalEthInProtocol();
```
