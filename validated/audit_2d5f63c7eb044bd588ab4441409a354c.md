### Title
No Staleness Check on Chainlink Price Feed Allows Stale Prices to Drive rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards every return value except `price`. There is no heartbeat timeout check, no `answeredInRound` check, and no `updatedAt` timestamp validation. A stale Chainlink price for any supported LST asset is silently accepted and used to compute rsETH minting amounts for depositors.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All five return values from `latestRoundData()` — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — are available, but only `answer` (aliased as `price`) is used. The `updatedAt` timestamp is never compared against `block.timestamp`, so there is no way for the contract to detect that the feed has not been updated within its expected heartbeat window (e.g., 1 hour for ETH/USD on Ethereum mainnet).

This oracle is registered as the price source for supported LST assets in `LRTOracle`:

```solidity
function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
    return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
}
``` [2](#0-1) 

`getAssetPrice` feeds directly into the rsETH minting calculation in `LRTDepositPool`:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

It also feeds `_getTotalEthInProtocol()`, which drives the rsETH price update in `_updateRsETHPrice()`:

```solidity
uint256 assetER = getAssetPrice(asset);
uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);
totalETHInProtocol += totalAssetAmt.mulWad(assetER);
``` [4](#0-3) 

By contrast, `ChainlinkOracleForRSETHPoolCollateral` — used in the pool contracts — at least checks `answeredInRound < roundID` and `timestamp == 0`, though it also lacks a heartbeat timeout:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
``` [5](#0-4) 

`ChainlinkPriceOracle` has none of these guards at all.

### Impact Explanation
When a Chainlink feed for a supported LST asset goes stale (e.g., during network congestion or an oracle outage), the last reported price — which may be significantly higher or lower than the true market price — is used without any rejection. If the stale price is inflated relative to the true value, depositors receive more rsETH than they are entitled to, diluting existing rsETH holders. If the stale price is deflated, depositors receive fewer rsETH tokens than they are owed. In both cases the contract fails to deliver the correct exchange rate it promises. This maps to **Low — contract fails to deliver promised returns, but doesn't lose value** under normal staleness conditions, with escalation toward yield theft if the price deviation is large and exploited deliberately.

### Likelihood Explanation
Chainlink feeds do go stale: network congestion, gas price spikes, or oracle node issues can delay updates beyond the published heartbeat. The ETH/USD feed on Ethereum has a 1-hour heartbeat and a 0.5% deviation threshold. Any period where neither condition triggers an update leaves the feed stale. Because `ChainlinkPriceOracle` applies zero timeout, even a multi-hour stale price is accepted silently. This is a known, recurring condition in DeFi and requires no privileged access to trigger.

### Recommendation
Add a configurable staleness timeout to `ChainlinkPriceOracle.getAssetPrice()` and revert if the feed has not been updated within that window:

```solidity
uint256 public constant STALENESS_TIMEOUT = 3600; // match the feed's heartbeat

function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (block.timestamp - updatedAt > STALENESS_TIMEOUT) revert StalePrice();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

The timeout should be set per-feed to match each feed's published heartbeat, or made configurable per asset.

### Proof of Concept
1. The Chainlink ETH/stETH (or any supported LST) feed goes stale — its last update was 2 hours ago and the true price has since dropped 3%.
2. A depositor calls `LRTDepositPool.depositAsset(stETH, amount, 0)`.
3. `_beforeDeposit` calls `getRsETHAmountToMint(stETH, amount)`.
4. `getRsETHAmountToMint` calls `lrtOracle.getAssetPrice(stETH)`, which calls `ChainlinkPriceOracle.getAssetPrice(stETH)`.
5. `ChainlinkPriceOracle` calls `priceFeed.latestRoundData()` and returns the 2-hour-old inflated price with no staleness check.
6. The depositor receives `amount * staleInflatedPrice / rsETHPrice` rsETH — more than the correct amount — diluting all existing rsETH holders. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTOracle.sol (L339-343)
```text
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L30-31)
```text
        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
```
