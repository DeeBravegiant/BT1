### Title
Chainlink Price Oracle Returns Stale/Invalid Data Without Validation - (`contracts/oracles/ChainlinkPriceOracle.sol`)

### Summary

`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `price`, performing zero validation on staleness, round completeness, or price sign. This is the direct Chainlink analog of M-10: the protocol consumes potentially stale or invalid oracle data, which propagates into the rsETH price used for deposits, withdrawals, and fee minting.

### Finding Description

`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

Three critical checks are absent:

1. **Staleness check** — `updatedAt` is silently discarded. If a Chainlink feed stops updating (network congestion, sequencer downtime on L2, feed deprecation), the last stored price is returned indefinitely with no revert.
2. **Round completeness check** — `answeredInRound >= roundId` is never verified. An incomplete round can return a price of `0`.
3. **Price sign/validity check** — `price` is cast directly to `uint256` with no `price > 0` guard. A zero or negative answer silently becomes a huge `uint256` value or zero.

By contrast, the sibling contract `ChainlinkOracleForRSETHPoolCollateral` in the same repo performs all three checks:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

The stale price flows directly into `LRTOracle._getTotalEthInProtocol()`, which iterates over all supported assets and calls `getAssetPrice()` for each: [3](#0-2) 

`_getTotalEthInProtocol()` feeds `_updateRsETHPrice()`, which computes the rsETH/ETH exchange rate stored in `rsETHPrice`: [4](#0-3) 

### Impact Explanation

A stale or invalid asset price causes `rsETHPrice` to be miscalculated. This value is used for:

- **Deposits** — rsETH minted per deposited asset is based on `rsETHPrice`.
- **Withdrawals** — `_calculatePayoutAmount` in `LRTWithdrawalManager` uses `rsETHPrice` and `assetPrice` to determine how much asset a user receives. [5](#0-4) 
- **Protocol fee minting** — fee rsETH minted is computed from the stale TVL delta. [6](#0-5) 

An inflated stale price causes users to receive fewer assets on withdrawal (loss of funds). A deflated stale price causes the protocol to over-pay on withdrawals or incorrectly trigger the downside-protection pause. **Impact: Medium — temporary freezing of funds / contract fails to deliver promised returns.**

### Likelihood Explanation

`updateRSETHPrice()` is a public, permissionless function callable by anyone: [7](#0-6) 

Chainlink feeds can go stale during L2 sequencer downtime, network congestion, or feed deprecation. No special attacker capability is required — any caller can trigger the price update at a moment when the feed is stale.

### Recommendation

Add staleness, round completeness, and price validity checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    if (block.timestamp - updatedAt > MAX_PRICE_AGE) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

### Proof of Concept

1. A Chainlink feed for a supported LST asset (e.g., stETH/ETH) stops updating due to L2 sequencer downtime.
2. An attacker (or any user) calls `LRTOracle.updateRSETHPrice()`.
3. `_getTotalEthInProtocol()` calls `ChainlinkPriceOracle.getAssetPrice(stETH)`.
4. `latestRoundData()` returns the last stored (stale) price — no revert occurs.
5. `rsETHPrice` is updated using the stale asset valuation.
6. Users withdrawing rsETH receive incorrect asset amounts based on the corrupted exchange rate.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-32)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
```

**File:** contracts/LRTOracle.sol (L87-88)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
```

**File:** contracts/LRTOracle.sol (L244-246)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
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

**File:** contracts/LRTWithdrawalManager.sol (L833-834)
```text
        uint256 currentReturn = (request.rsETHUnstaked * rsETHPrice) / assetPrice;
        return (request.expectedAssetAmount < currentReturn) ? request.expectedAssetAmount : currentReturn;
```
