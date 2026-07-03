### Title
Stale Chainlink Price Feed in `ChainlinkPriceOracle.getAssetPrice()` Enables Over-Minting of rsETH at Depositor's Benefit - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all staleness-related return values (`updatedAt`, `answeredInRound`). A stale price feed that reports an inflated LST/ETH rate allows any unprivileged depositor to mint more rsETH than their deposited assets are actually worth, diluting all existing rsETH holders.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

The `updatedAt` timestamp and `answeredInRound` fields are silently discarded. No heartbeat deadline check (e.g., `block.timestamp - updatedAt > MAX_DELAY`) and no round-completeness check (`answeredInRound >= roundId`) are performed. [1](#0-0) 

This is the oracle used by `LRTOracle.getAssetPrice()`, which delegates directly to the registered `IPriceFetcher`:

```solidity
function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
    return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
}
``` [2](#0-1) 

`LRTDepositPool.getRsETHAmountToMint()` uses this price to compute how many rsETH tokens to mint per deposited LST:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [3](#0-2) 

This value is then used directly in `_beforeDeposit()` and `depositAsset()` to mint rsETH to the caller: [4](#0-3) 

The inconsistency is confirmed by the fact that `ChainlinkOracleForRSETHPoolCollateral` — used for pool collateral pricing — **does** implement proper staleness and round-completeness checks:

```solidity
(uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
    AggregatorV3Interface(oracle).latestRoundData();
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [5](#0-4) 

The core deposit oracle lacks these same protections.

### Impact Explanation
When a Chainlink feed for a supported LST (e.g., stETH/ETH) goes stale at a price higher than the current market rate — which occurs during oracle node downtime, network congestion, or a depeg event where the price has moved but the feed has not yet updated — any depositor can call `depositAsset()` with that LST and receive rsETH computed against the inflated stale price. The excess rsETH minted represents value extracted from all existing rsETH holders, whose proportional claim on the protocol's ETH TVL is diluted. The `minRSETHAmountExpected` slippage guard protects only the depositor (ensures they get at least what they expect), not the protocol against over-minting.

**Impact: High — Theft of yield/value from existing rsETH holders; in a severe depeg scenario (e.g., LST price drops 10–20% while feed is stale), this approaches Critical (direct theft of user funds at-rest).**

### Likelihood Explanation
Chainlink feeds have documented heartbeat intervals (e.g., 1 hour for stETH/ETH on mainnet) and deviation thresholds. During periods of low volatility, feeds may not update for the full heartbeat window. During network congestion or oracle node failures, feeds can go stale for extended periods. The protocol already deploys on L2 chains (evidenced by `RSETHPool`, `TACWETHBridge`, `RSETHPoolV3ExternalBridge`), where sequencer downtime can additionally freeze feed updates. The scenario is realistic and has occurred in production DeFi protocols.

**Likelihood: Medium** — requires a stale feed window coinciding with a depositor acting opportunistically, but no privileged access is needed.

### Recommendation
Add staleness and round-completeness checks to `ChainlinkPriceOracle.getAssetPrice()`, mirroring the pattern already used in `ChainlinkOracleForRSETHPoolCollateral`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) = priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (block.timestamp - updatedAt > MAX_STALENESS_DELAY) revert StalePrice();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`MAX_STALENESS_DELAY` should be set per-asset based on the feed's documented heartbeat interval. For L2 deployments, additionally integrate a Chainlink sequencer uptime feed check before consuming any price data.

### Proof of Concept

1. Assume stETH/ETH Chainlink feed has a 1-hour heartbeat. The feed last updated at `T=0` with price `1.00 ETH`. At `T=50min`, stETH depegs to `0.90 ETH` on-chain, but the feed has not yet updated (price movement < deviation threshold, or oracle node is lagging).

2. Attacker calls:
   ```solidity
   LRTDepositPool.depositAsset(stETH, 1000e18, minRSETH, "");
   ```

3. `getRsETHAmountToMint(stETH, 1000e18)` calls `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns `1.00e18` (stale). [6](#0-5) 

4. `rsethAmountToMint = (1000e18 * 1.00e18) / rsETHPrice` — computed at the stale inflated rate. [3](#0-2) 

5. Attacker receives rsETH worth `1000 ETH` of protocol claim, but deposited stETH worth only `900 ETH` at current market price.

6. The `100 ETH` difference in value is extracted from existing rsETH holders' proportional share of the protocol TVL. No admin action or privileged role is required.

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

**File:** contracts/LRTDepositPool.sol (L99-118)
```text
    function depositAsset(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedERC20Token(asset)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);

        emit AssetDeposit(msg.sender, asset, depositAmount, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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
