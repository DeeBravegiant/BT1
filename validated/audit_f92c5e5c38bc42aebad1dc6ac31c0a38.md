### Title
No Time-Based Staleness Check on Chainlink Price Feeds Allows Stale Prices to Be Consumed - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls Chainlink's `latestRoundData()` but never validates the returned `updatedAt` timestamp against `block.timestamp`. There is no heartbeat/staleness window enforced at all. Any Chainlink feed that stops updating will silently return its last known price indefinitely, and the protocol will consume it as if it were fresh.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the price from a Chainlink aggregator but discards the `updatedAt` return value entirely:

```solidity
// contracts/oracles/ChainlinkPriceOracle.sol L52
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

No check of the form `if (block.timestamp - updatedAt > MAX_STALENESS) revert` exists anywhere in the function. The `updatedAt` field is silently dropped via the `(, int256 price,,,)` destructuring pattern.

This price is consumed by `LRTOracle.getAssetPrice()`:

```solidity
// contracts/LRTOracle.sol L157
return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
``` [2](#0-1) 

Which is then used in `LRTDepositPool.getRsETHAmountToMint()` to determine how many rsETH tokens a depositor receives:

```solidity
// contracts/LRTDepositPool.sol L520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

And in `LRTWithdrawalManager._createUnlockParams()` to determine withdrawal payout amounts:

```solidity
// contracts/LRTWithdrawalManager.sol L848
assetPrice: lrtOracle.getAssetPrice(asset),
``` [4](#0-3) 

A secondary instance exists in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, which uses only the deprecated `answeredInRound < roundID` staleness check — a check that is always false on OCR-based Chainlink feeds (where `answeredInRound == roundID` by design) — and performs no time-based validation:

```solidity
// contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol L30
if (answeredInRound < roundID) revert StalePrice();
``` [5](#0-4) 

This oracle is used in pool contracts (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) to compute `tokenToETHRate` for token deposits, which determines how much wrsETH is minted per token deposited.

### Impact Explanation
If a Chainlink feed stops updating (e.g., due to network congestion, sequencer downtime on L2, or oracle node failure), the protocol will silently consume the last reported price as if it were current. If the stale price is inflated relative to the true market price, depositors receive more rsETH than they are entitled to, diluting existing rsETH holders and constituting a direct theft of value from them. If the stale price is deflated, withdrawers receive fewer assets than they are owed. In both cases the rsETH/ETH exchange rate computed in `_updateRsETHPrice()` is corrupted, affecting all downstream accounting.

**Impact: Low** — Contract fails to deliver promised returns (incorrect rsETH minting/redemption amounts based on stale prices). In a scenario where the stale price diverges significantly, the price-deviation circuit breaker in `_updateRsETHPrice()` may trigger an erroneous protocol pause, escalating to **Medium** (temporary freezing of funds). [6](#0-5) 

### Likelihood Explanation
Chainlink feeds can go stale during periods of L2 sequencer downtime, extreme network congestion, or oracle node failures. The protocol is deployed on Ethereum mainnet and potentially other chains. The absence of any time-based check means the window of exposure is unbounded — the stale price persists until the feed resumes. Any unprivileged depositor or withdrawer interacting with the protocol during a stale-feed period triggers the vulnerable path.

### Recommendation
Add a per-asset configurable staleness threshold and validate `updatedAt` in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
mapping(address asset => uint256 maxStaleness) public maxStalenessPerAsset;

function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,, uint256 updatedAt,) = priceFeed.latestRoundData();
    uint256 staleness = maxStalenessPerAsset[asset];
    if (staleness > 0 && block.timestamp - updatedAt > staleness) revert StalePrice();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Replace the deprecated `answeredInRound < roundID` check in `ChainlinkOracleForRSETHPoolCollateral.getRate()` with a time-based check using the appropriate heartbeat for the specific feed and chain.

### Proof of Concept
1. Chainlink's stETH/ETH feed (or any supported asset feed) stops updating due to L2 sequencer downtime or oracle node failure. The last reported price was `1.05e18` (stETH at a 5% premium to ETH).
2. The true market price of stETH drops to `0.98e18` during the outage.
3. A depositor calls `LRTDepositPool.depositAsset(stETH, 100e18, 0)`.
4. `getRsETHAmountToMint(stETH, 100e18)` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns `1.05e18` (stale, no revert).
5. `rsethAmountToMint = (100e18 * 1.05e18) / rsETHPrice` — the depositor receives rsETH computed at the inflated stale price, overpaying existing holders.
6. No revert occurs at any step because `updatedAt` is never checked against `block.timestamp`. [1](#0-0) [7](#0-6)

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

**File:** contracts/LRTOracle.sol (L270-281)
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

**File:** contracts/LRTWithdrawalManager.sol (L846-850)
```text
        return UnlockParams({
            rsETHPrice: lrtOracle.rsETHPrice(),
            assetPrice: lrtOracle.getAssetPrice(asset),
            totalAvailableAssets: unstakingVault.balanceOf(asset)
        });
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
