### Title
Missing Staleness Check in Chainlink Price Feed Allows Stale Price Acceptance - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but completely discards the `updatedAt` timestamp, performing zero staleness validation. A stale Chainlink price for any supported LST asset propagates directly into rsETH price computation, enabling depositors to mint rsETH at a manipulated exchange rate.

### Finding Description
The `getAssetPrice` function in `ChainlinkPriceOracle` retrieves the Chainlink price but ignores all return values except `price`:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
```

The `updatedAt` field — which is the standard mechanism for detecting stale Chainlink data — is silently discarded. No `block.timestamp - updatedAt > MAX_STALENESS` guard exists anywhere in the function. [1](#0-0) 

This price is consumed by `LRTOracle.getAssetPrice()`, which delegates directly to the registered `IPriceFetcher`: [2](#0-1) 

`_getTotalEthInProtocol()` iterates all supported assets and multiplies each asset's balance by its stale price to compute total ETH in the protocol: [3](#0-2) 

The resulting `totalETHInProtocol` feeds directly into `newRsETHPrice` computation: [4](#0-3) 

`updateRSETHPrice()` is a public, permissionless function callable by any address: [5](#0-4) 

When a depositor calls `depositAsset()`, the rsETH mint amount is computed using the current (potentially stale) `rsETHPrice`: [6](#0-5) 

### Impact Explanation
If a Chainlink feed for a supported LST asset (e.g., stETH/ETH) goes stale at a price lower than the true market price, `_getTotalEthInProtocol()` underestimates total ETH, causing `rsETHPrice` to be computed below its true value. A depositor who calls `depositAsset()` at this depressed rsETH price receives more rsETH shares than they are entitled to. When the feed recovers and `rsETHPrice` corrects upward, the attacker's excess shares dilute all existing rsETH holders — constituting theft of unclaimed yield. Conversely, a stale price above true value causes the downside protection logic to potentially trigger an erroneous protocol pause, temporarily freezing funds.

**Impact: High — Theft of unclaimed yield / Medium — Temporary freezing of funds**

### Likelihood Explanation
Chainlink feeds for LST assets (stETH/ETH, cbETH/ETH, rETH/ETH) have documented heartbeat intervals (typically 1–24 hours) and deviation thresholds. During periods of low volatility or network congestion, feeds can lag significantly within their heartbeat window while still returning data that passes the `answeredInRound >= roundID` check. Because there is no time-based staleness bound at all, any lag within the heartbeat window is silently accepted. This is a realistic, non-adversarial condition that occurs on mainnet.

### Recommendation
Add a per-asset or global staleness threshold and validate `updatedAt` against it in `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
uint256 constant MAX_STALENESS = 3600; // or per-asset mapping

function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);
    (, int256 price,, uint256 updatedAt,) = priceFeed.latestRoundData();
    if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();
    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

Different assets have different Chainlink heartbeats (e.g., stETH/ETH: 86400s, ETH/USD: 3600s), so a per-asset staleness mapping is preferable to a single global constant — directly mirroring the recommendation in the referenced external report.

### Proof of Concept
1. Chainlink's stETH/ETH feed enters a period of low volatility and does not update for 23 hours (within its 24-hour heartbeat — no on-chain staleness revert occurs).
2. During this window, stETH appreciates 0.5% but the feed still reports the old lower price.
3. Attacker calls `LRTOracle.updateRSETHPrice()` (permissionless) — rsETH price is computed using the stale, undervalued stETH price, producing a depressed `rsETHPrice`.
4. Attacker calls `LRTDepositPool.depositAsset(stETH, largeAmount, 0, "")` — receives excess rsETH shares at the artificially low exchange rate.
5. Feed updates; anyone calls `updateRSETHPrice()` again — `rsETHPrice` corrects upward.
6. Attacker holds rsETH worth more ETH than they deposited, at the expense of existing holders.

The entry path is fully permissionless: `depositAsset` → `_beforeDeposit` → `getRsETHAmountToMint` → `LRTOracle.getAssetPrice` → `ChainlinkPriceOracle.getAssetPrice` (no staleness check). [1](#0-0) [7](#0-6)

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

**File:** contracts/LRTOracle.sol (L249-250)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L331-349)
```text
    function _getTotalEthInProtocol() private view returns (uint256 totalETHInProtocol) {
        address lrtDepositPoolAddr = lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL);
        address[] memory supportedAssets = lrtConfig.getSupportedAssetList();
        uint256 supportedAssetCount = supportedAssets.length;

        for (uint16 assetIdx; assetIdx < supportedAssetCount;) {
            address asset = supportedAssets[assetIdx];
            // assetER is in 1e18 precision (1.0 = 1e18)
            uint256 assetER = getAssetPrice(asset);
            // totalAssetAmt is in 1e18 precision (standard token decimals)
            uint256 totalAssetAmt = ILRTDepositPool(lrtDepositPoolAddr).getTotalAssetDeposits(asset);

            totalETHInProtocol += totalAssetAmt.mulWad(assetER);

            unchecked {
                ++assetIdx;
            }
        }
    }
```

**File:** contracts/LRTDepositPool.sol (L100-118)
```text
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
