### Title
Missing Chainlink Oracle Staleness and Validity Checks Allow Stale Price Acceptance - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `price`, performing zero validation on staleness (`updatedAt`), round completeness (`answeredInRound` vs `roundId`), or price sign (`price > 0`). This is the direct analog of the ConnextPriceOracle finding. The same codebase already implements all three checks in `ChainlinkOracleForRSETHPoolCollateral`, making the omission in `ChainlinkPriceOracle` a clear inconsistency with known-correct patterns.

### Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol`, `getAssetPrice()` reads from a Chainlink feed as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values of `latestRoundData()` — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — are available, but only `answer` (aliased as `price`) is used. The following checks are entirely absent:

1. **Staleness check**: `block.timestamp - updatedAt > threshold` — the price may be arbitrarily old.
2. **Stale round check**: `answeredInRound < roundId` — the answer may belong to a prior, incomplete round.
3. **Non-negative price check**: `price <= 0` — a zero or negative `int256` is cast directly to `uint256`, producing either zero or a massive wraparound value.

By contrast, `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` — another Chainlink wrapper in the same repository — correctly implements all three guards:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

`ChainlinkPriceOracle.getAssetPrice()` is the price source registered in `LRTOracle` for supported LST assets (stETH, cbETH, etc.). It is consumed in two critical paths:

- **Deposit minting** (`LRTDepositPool.getRsETHAmountToMint()`): `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()` — a stale inflated price mints excess rsETH to the depositor.
- **rsETH price update** (`LRTOracle._updateRsETHPrice()` → `_getTotalEthInProtocol()`): `totalETHInProtocol += totalAssetAmt.mulWad(assetER)` — a stale price distorts the protocol's total ETH accounting, causing `rsETHPrice` to be set incorrectly, diluting or inflating existing holders.

### Impact Explanation
If a Chainlink feed becomes stale during network congestion, oracle downtime, or a depeg event, the last reported price is accepted unconditionally. An attacker who observes that the on-chain stale price is higher than the current market price (e.g., stETH depegs from 1.0 ETH to 0.97 ETH but the oracle is frozen at 1.0 ETH) can:

1. Buy the depegged asset cheaply on the open market.
2. Deposit it into `LRTDepositPool`, receiving rsETH calculated at the stale 1.0 ETH rate.
3. Redeem rsETH for ETH at the inflated rate, extracting value from existing rsETH holders.

This constitutes theft of yield/value from existing rsETH holders and temporary mispricing of the protocol's NAV. A zero or negative price return would additionally cause a revert or catastrophic uint256 wraparound, temporarily freezing deposits.

**Impact**: Medium — temporary freezing of funds (if price is zero/negative causing revert) and theft of unclaimed yield / share dilution (if stale inflated price is used).

### Likelihood Explanation
Chainlink feeds do go stale during periods of low volatility (heartbeat not triggered), network congestion, or sequencer downtime on L2s. The protocol is deployed on multiple L2s (Arbitrum, Optimism, Base, Scroll, etc.) where sequencer outages are a known risk. No admin action is required; any depositor can exploit the window passively.

### Recommendation
Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral.getRate()` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > STALENESS_THRESHOLD) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`STALENESS_THRESHOLD` should be set per-feed based on the feed's documented heartbeat interval (e.g., 3600 seconds for a 1-hour heartbeat feed).

### Proof of Concept

**Vulnerable code** — all return values except `price` are silently discarded: [1](#0-0) 

**Correct pattern already present in the same repo** — all three guards implemented: [2](#0-1) 

**Deposit minting path consuming the stale price** — `getAssetPrice` feeds directly into rsETH mint calculation: [3](#0-2) 

**rsETH price update path consuming the stale price** — stale asset price distorts total ETH in protocol: [4](#0-3)

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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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
