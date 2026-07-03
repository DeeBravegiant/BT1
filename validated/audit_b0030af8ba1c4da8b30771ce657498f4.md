### Title
Chainlink `latestRoundData()` Missing Staleness Check Allows Stale Price to Drive rsETH Minting - (File: `contracts/oracles/ChainlinkPriceOracle.sol`)

---

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls Chainlink's `latestRoundData()` but silently discards the `updatedAt` timestamp and `answeredInRound` values, performing zero staleness validation. This stale price is then consumed by `LRTOracle` to compute the rsETH/ETH exchange rate, which directly governs how many rsETH tokens are minted to depositors in `LRTDepositPool`.

---

### Finding Description

In `contracts/oracles/ChainlinkPriceOracle.sol`, the `getAssetPrice()` function fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values of `latestRoundData()` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. The code destructures only `answer` (as `price`) and discards `roundId`, `startedAt`, `updatedAt`, and `answeredInRound` entirely. There is no check that:
- `updatedAt != 0` (incomplete round guard)
- `block.timestamp - updatedAt <= maxStaleness` (freshness guard)
- `answeredInRound >= roundId` (round completeness guard)

This is in direct contrast to `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol`, which correctly validates:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
```

The `ChainlinkPriceOracle` is registered in `LRTOracle` via `assetPriceOracle[asset]` and is called through `LRTOracle.getAssetPrice()`:

```solidity
return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
```

`LRTOracle._updateRsETHPrice()` uses these per-asset prices to compute the total ETH value of the protocol, which determines `rsETHPrice`. `LRTDepositPool` uses `rsETHPrice` to calculate how many rsETH tokens to mint per deposited LST.

---

### Impact Explanation

If a Chainlink feed goes stale (e.g., due to L2 sequencer downtime, oracle node failure, or extreme market volatility causing heartbeat gaps), `ChainlinkPriceOracle.getAssetPrice()` will silently return the last recorded price with no revert. If the stale price is lower than the true current price of the LST collateral, the rsETH exchange rate will be understated, and depositors receive more rsETH than they are entitled to — effectively stealing value from existing rsETH holders. Conversely, if the stale price is higher, depositors receive fewer rsETH tokens than they deserve, causing a loss to the depositor. Either direction constitutes a share/asset mis-accounting that can be exploited by a depositor who monitors oracle staleness.

**Impact: Low to Medium** — Contract fails to deliver promised returns / temporary incorrect rsETH minting. In an extreme staleness scenario (e.g., prolonged sequencer outage on an L2 deployment), this could escalate to theft of unclaimed yield or protocol insolvency if the rate diverges significantly.

---

### Likelihood Explanation

Chainlink feeds do occasionally go stale, particularly on L2 networks during sequencer downtime. The protocol is deployed on multiple L2s (Arbitrum, Optimism, Base). A sophisticated depositor can monitor on-chain oracle `updatedAt` values and time a deposit during a staleness window. No privileged access is required — any depositor can call `LRTDepositPool.depositAsset()`.

---

### Recommendation

Add staleness and round-completeness checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_STALENESS` should be set per-feed based on the Chainlink heartbeat (e.g., 3600 seconds for 1-hour heartbeat feeds).

---

### Proof of Concept

1. A Chainlink LST/ETH price feed used by `ChainlinkPriceOracle` goes stale (e.g., last updated 4 hours ago, true price has dropped 5%).
2. `ChainlinkPriceOracle.getAssetPrice(lstAsset)` returns the stale, inflated price with no revert.
3. `LRTOracle._updateRsETHPrice()` computes a higher-than-actual total ETH value, inflating `rsETHPrice`.
4. An attacker calls `LRTDepositPool.depositAsset(lstAsset, amount)`.
5. The deposit pool uses the inflated `rsETHPrice` to mint fewer rsETH than the depositor deserves — or, if the stale price is lower than reality, mints more rsETH than deserved, diluting existing holders.
6. No special permissions are required; any externally reachable depositor can trigger this path.

**Root cause line:** [1](#0-0) 

**Contrast with correct staleness check:** [2](#0-1) 

**Oracle consumption in LRTOracle:** [3](#0-2)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```
