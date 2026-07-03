### Title
No Staleness Check on Chainlink Price Data Allows Stale Asset Prices to Corrupt rsETH Exchange Rate - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls Chainlink's `latestRoundData()` but discards all staleness-related return values (`updatedAt`, `answeredInRound`, `roundId`). A stale Chainlink feed will silently propagate an outdated asset price into the rsETH exchange rate, which is used for every deposit and withdrawal in the protocol.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values of `latestRoundData()` are `(roundId, answer, startedAt, updatedAt, answeredInRound)`. The code destructures only `answer` and silently discards `roundId`, `startedAt`, `updatedAt`, and `answeredInRound`. There is no check of the form:
- `if (answeredInRound < roundId) revert StalePrice();`
- `if (block.timestamp - updatedAt > heartbeat) revert StalePrice();`

This price is consumed by `LRTOracle.getAssetPrice()`, which feeds directly into `LRTOracle._getTotalEthInProtocol()`, which is the sole input to `_updateRsETHPrice()`. The resulting `rsETHPrice` is the exchange rate used for all deposits and withdrawals.

Contrast this with `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which at least checks `answeredInRound < roundID` and `timestamp == 0`, demonstrating the project is aware of the staleness pattern but did not apply it to the primary oracle.

### Impact Explanation
If a Chainlink feed for any supported LST asset (e.g., stETH/ETH, cbETH/ETH) goes stale — due to sequencer downtime, node failure, or extreme network congestion — `ChainlinkPriceOracle` will return the last recorded price with no revert or warning. `_updateRsETHPrice()` will then compute an incorrect `rsETHPrice`. Depositors will receive rsETH at a wrong rate (too many or too few tokens), and withdrawers will receive incorrect asset amounts. This constitutes the protocol failing to deliver promised returns and, in the direction of an inflated stale price, constitutes theft of yield from existing rsETH holders.

**Impact: Low–Medium** (contract fails to deliver promised returns; in the worst direction, theft of unclaimed yield from existing holders).

### Likelihood Explanation
Chainlink feeds do occasionally go stale during network stress events or oracle node outages. The affected oracle is the primary price source for all supported LST assets. The `updateRSETHPrice()` function is public and callable by anyone, meaning the stale price can be committed to state at any time without admin intervention.

**Likelihood: Low** (requires a Chainlink feed outage, which is uncommon but historically observed).

### Recommendation
Add both a round-completeness check and a time-based heartbeat check in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (block.timestamp - updatedAt > MAX_HEARTBEAT) revert StalePrice(); // e.g. 1 hours or 24 hours per feed

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

The `MAX_HEARTBEAT` should match each feed's documented heartbeat (typically 1 hour for ETH/USD, 24 hours for LST/ETH feeds on Ethereum mainnet).

### Proof of Concept

**Attacker-controlled entry path:**

1. A Chainlink LST/ETH feed goes stale (e.g., last updated 30+ hours ago).
2. Anyone calls `LRTOracle.updateRSETHPrice()` (public, no access control).
3. `_updateRsETHPrice()` calls `_getTotalEthInProtocol()`.
4. `_getTotalEthInProtocol()` calls `LRTOracle.getAssetPrice(asset)` for each supported asset.
5. `LRTOracle.getAssetPrice()` delegates to `ChainlinkPriceOracle.getAssetPrice(asset)`.
6. `ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` and returns the stale price without any check.
7. The stale price inflates or deflates `totalETHInProtocol`, producing an incorrect `newRsETHPrice`.
8. `rsETHPrice` is updated to the incorrect value and used for all subsequent deposits and withdrawals.

**Relevant code references:**

`ChainlinkPriceOracle.getAssetPrice()` — no staleness validation: [1](#0-0) 

`LRTOracle.getAssetPrice()` — delegates directly to the unchecked oracle: [2](#0-1) 

`_getTotalEthInProtocol()` — uses the stale price to compute total ETH: [3](#0-2) 

`updateRSETHPrice()` — public entry point, no access control: [4](#0-3) 

Contrast with `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which does apply partial staleness checks: [5](#0-4)

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
