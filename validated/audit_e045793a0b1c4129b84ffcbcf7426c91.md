### Title
Missing Chainlink Price Feed Staleness Validation Allows Stale Prices to Inflate rsETH Minting - (File: contracts/oracles/ChainlinkPriceOracle.sol)

### Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all validation fields (`updatedAt`, `roundId`, `answeredInRound`). A stale or incomplete Chainlink round is silently accepted, feeding an incorrect asset price into the rsETH minting calculation and allowing depositors to mint rsETH at a manipulated rate.

### Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the Chainlink price as follows:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values of `latestRoundData()` are available — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — but only `answer` is used. No check is performed on:

- **`updatedAt`**: whether the price was updated recently (staleness)
- **`answeredInRound` vs `roundId`**: whether the round completed successfully

By contrast, the protocol's own `ChainlinkOracleForRSETHPoolCollateral` contract (used for pool collateral) performs all three validations:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The unvalidated price from `ChainlinkPriceOracle` flows directly into the rsETH minting path:

1. `ChainlinkPriceOracle.getAssetPrice(asset)` → called by `LRTOracle.getAssetPrice(asset)`
2. `LRTOracle.getAssetPrice(asset)` → called by `LRTDepositPool.getRsETHAmountToMint()`
3. `getRsETHAmountToMint()` → called by `_beforeDeposit()` → called by `depositAsset()` / `depositETH()`

Additionally, `LRTOracle._getTotalEthInProtocol()` calls `getAssetPrice(asset)` for every supported asset to compute the total ETH TVL, which feeds `_updateRsETHPrice()`. A stale inflated price here inflates the computed TVL, causing excess protocol fee rsETH to be minted to the treasury.

### Impact Explanation
**High — Theft of unclaimed yield / share dilution.**

If a Chainlink feed for a supported LST asset (e.g., stETH/ETH) becomes stale with an inflated last-known price (e.g., during oracle downtime or network congestion), any depositor calling `depositAsset()` receives more rsETH than their deposit is worth:

```
rsethAmountToMint = (depositAmount * staleInflatedAssetPrice) / rsETHPrice
```

The excess rsETH minted to the depositor dilutes the share value of all existing rsETH holders, constituting theft of yield from existing holders. The same stale price fed into `_updateRsETHPrice()` inflates `totalETHInProtocol`, causing the protocol to compute a falsely elevated TVL increase and mint excess fee rsETH to the treasury.

### Likelihood Explanation
Chainlink feeds can go stale during periods of low volatility (heartbeat not triggered), network congestion, or oracle node downtime. The affected assets are mainnet LST tokens (stETH, cbETH, rETH, etc.) whose Chainlink feeds have heartbeat intervals of 24 hours. A 24-hour stale window is a realistic scenario. Any unprivileged depositor can trigger the vulnerable path by calling `depositAsset()` or `depositETH()` at any time — no special role or setup is required.

### Recommendation
Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
// Optionally: if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();
```

### Proof of Concept

**Vulnerable code — no validation:**

`ChainlinkPriceOracle.getAssetPrice()` discards all validation fields: [1](#0-0) 

**Correct pattern — same codebase, different contract:**

`ChainlinkOracleForRSETHPoolCollateral.getRate()` validates all three conditions: [2](#0-1) 

**Stale price propagates to rsETH mint calculation:**

`LRTDepositPool.getRsETHAmountToMint()` uses the unvalidated price directly: [3](#0-2) 

**Stale price also inflates TVL used for fee minting:**

`LRTOracle._getTotalEthInProtocol()` calls `getAssetPrice()` per asset with no staleness guard: [4](#0-3) 

**Entry point — unprivileged depositor:**

`depositAsset()` is callable by any user and triggers the full vulnerable path: [5](#0-4)

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-36)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
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
