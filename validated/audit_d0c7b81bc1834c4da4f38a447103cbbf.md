### Title
Unvalidated `latestRoundData` Return Values Allow Stale Prices to Corrupt rsETH Minting and Price Updates - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `price`, performing no staleness checks. This stale price propagates directly into rsETH minting calculations for every depositor and into the public `updateRSETHPrice()` function, which can corrupt the stored `rsETHPrice` and trigger an unintended protocol-wide pause.

### Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol`, `getAssetPrice()` calls `latestRoundData()` but ignores `roundId`, `updatedAt`, and `answeredInRound`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

No check is made that `updatedAt != 0` (incomplete round), that `answeredInRound >= roundId` (stale round), or that the timestamp is within an acceptable freshness window. By contrast, `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` in the same repository correctly validates all three conditions:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The stale price returned by `ChainlinkPriceOracle.getAssetPrice()` flows through two critical paths:

**Path 1 — Deposit minting (user-triggered):**
`LRTDepositPool.depositAsset()` / `depositETH()` → `_beforeDeposit()` → `getRsETHAmountToMint()` → `lrtOracle.getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice()`. [3](#0-2) 

**Path 2 — rsETH price update (publicly callable):**
`LRTOracle.updateRSETHPrice()` (no access control) → `_updateRsETHPrice()` → `_getTotalEthInProtocol()` → `getAssetPrice(asset)` → `ChainlinkPriceOracle.getAssetPrice()`. [4](#0-3) [5](#0-4) 

### Impact Explanation
**Medium — Temporary freezing of funds.**

`_updateRsETHPrice()` computes `newRsETHPrice` from the stale total ETH value. If the stale price deviates enough from `highestRsethPrice`, the downside-protection branch fires:

```solidity
if (isPriceDecreaseOffLimit) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
``` [6](#0-5) 

This pauses `LRTDepositPool` and `LRTWithdrawalManager`, temporarily freezing all deposits and withdrawals for all users until an admin manually unpauses. Additionally, a stale inflated price causes depositors to receive more rsETH than they are entitled to, diluting existing rsETH holders' share of protocol TVL.

### Likelihood Explanation
Chainlink feeds can return stale data during network congestion, sequencer downtime (on L2s where this contract is deployed), or when a feed's heartbeat lapses. The trigger requires no attacker action beyond calling the public `updateRSETHPrice()` after a stale round is published. The protocol is deployed on multiple chains (Arbitrum, Optimism, Base, etc.) where L2 sequencer outages are a known historical occurrence, making this a realistic scenario.

### Recommendation
- **Short term**: In `ChainlinkPriceOracle.getAssetPrice()`, capture and validate all `latestRoundData` return values:
  ```solidity
  (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
      priceFeed.latestRoundData();
  require(answeredInRound >= roundId, "Stale price");
  require(updatedAt != 0, "Incomplete round");
  require(block.timestamp - updatedAt <= MAX_STALENESS, "Price too old");
  require(price > 0, "Non-positive price");
  ```
- **Long term**: Implement per-feed configurable staleness thresholds and a circuit-breaker fallback (e.g., pause oracle reads) for all Chainlink integrations across all supported chains.

### Proof of Concept
1. Chainlink's LST/ETH feed (e.g., stETH/ETH) enters a stale round — `updatedAt` is hours old and `answeredInRound < roundId`.
2. Anyone calls `LRTOracle.updateRSETHPrice()` (no access control).
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)`, which returns the stale (e.g., artificially depressed) price without reverting.
4. `newRsETHPrice` is computed lower than `highestRsethPrice` by more than `pricePercentageLimit`.
5. The condition at `LRTOracle.sol:277` triggers: `LRTDepositPool`, `LRTWithdrawalManager`, and `LRTOracle` are all paused.
6. All user deposits and withdrawals are frozen until an admin intervenes. [7](#0-6) [8](#0-7)

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

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
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
