### Title
Unsafe `int256`-to-`uint256` Cast of Chainlink Price Without Sign Check Causes Deposit DoS - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` casts the `int256 answer` returned by Chainlink's `latestRoundData()` directly to `uint256` without validating that the value is positive. A negative price — possible during Chainlink circuit-breaker events or feed anomalies — silently wraps to a near-`2^256` value, causing an arithmetic overflow revert in Solidity 0.8 and permanently bricking all asset deposits that rely on this oracle until the feed recovers.

### Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol`, the `getAssetPrice` function reads the Chainlink price and immediately casts it:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

There is no guard of the form `if (price <= 0) revert InvalidPrice();` before the cast. If `price` is negative (e.g., `-1`), `uint256(-1)` evaluates to `2^256 - 1`. The subsequent multiplication `(2^256 - 1) * 1e18` overflows and reverts under Solidity 0.8's checked arithmetic.

By contrast, the sister contract `ChainlinkOracleForRSETHPoolCollateral` correctly validates the sign before casting:

```solidity
if (ethPrice <= 0) revert InvalidPrice();
uint256 normalizedPrice = uint256(ethPrice) * 1e18 / ...
``` [2](#0-1) 

The vulnerable `ChainlinkPriceOracle` is the L1 oracle used by `LRTOracle` to price supported LST assets (e.g., stETH, cbETH). `LRTOracle.getRSETHPrice()` calls `getAssetPrice` for every supported asset to compute the total ETH value backing rsETH, which is then used by `LRTDepositPool` to determine how much rsETH to mint per deposit. [3](#0-2) 

### Impact Explanation
When any supported asset's Chainlink feed returns a non-positive price, `getAssetPrice` reverts. This propagates up through `LRTOracle.getRSETHPrice()` → `LRTDepositPool.getRsETHAmountToMint()` → `depositAsset()` / `depositETH()`, causing all deposits to revert. The result is a **temporary freeze of deposit functionality** for all users until the Chainlink feed recovers. This matches the "Temporary freezing of funds" impact category.

### Likelihood Explanation
Chainlink feeds can return zero or negative values during circuit-breaker events (e.g., when the underlying asset price moves outside the feed's configured min/max bounds, the aggregator returns the boundary value, which for some feeds is `0` or a negative sentinel). This is a known, documented Chainlink edge case. It does not require any attacker action — it can occur organically during market stress, which is precisely when the protocol is most critical to remain operational.

### Recommendation
Add a sign and zero check before casting, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
if (price <= 0) revert InvalidPrice();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

Additionally, consider adding staleness checks (`updatedAt`, `answeredInRound`) as `ChainlinkOracleForRSETHPoolCollateral` does.

### Proof of Concept
1. A supported LST asset (e.g., stETH) has its Chainlink feed hit a circuit-breaker, returning `price = 0` or `price = -1`.
2. Any user calls `LRTDepositPool.depositAsset(stETH, amount, minRsETH, "")`.
3. Internally, `getRsETHAmountToMint` calls `LRTOracle.getRSETHPrice()`.
4. `getRSETHPrice` calls `ChainlinkPriceOracle.getAssetPrice(stETH)`.
5. `uint256(-1) * 1e18` overflows → revert.
6. All deposits revert. The protocol cannot accept new deposits until the feed normalizes. [3](#0-2)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L32-34)
```text
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());
```
