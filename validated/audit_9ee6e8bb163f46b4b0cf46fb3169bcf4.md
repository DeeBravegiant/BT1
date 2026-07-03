Audit Report

## Title
Missing Chainlink Price Feed Staleness Validation in `ChainlinkPriceOracle.getAssetPrice()` Allows Stale Prices to Over-Mint rsETH - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but silently discards `updatedAt`, `roundId`, and `answeredInRound`, accepting stale or incomplete Chainlink rounds without reversion. The same codebase already implements the correct validation pattern in `ChainlinkOracleForRSETHPoolCollateral.getRate()`. A stale inflated price fed into the rsETH minting formula causes depositors to receive excess rsETH, diluting the share value of all existing rsETH holders.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` at `contracts/oracles/ChainlinkPriceOracle.sol` L52–54 reads:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

All five return values (`roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound`) are available but only `answer` is used. No check is performed on `updatedAt` (staleness), `answeredInRound < roundId` (incomplete round), or `price <= 0` (invalid price).

By contrast, `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` L27–32 performs all three validations:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The unvalidated price propagates through the following call chain:

1. `ChainlinkPriceOracle.getAssetPrice(asset)` → `LRTOracle.getAssetPrice(asset)` (L156–158)
2. `LRTOracle.getAssetPrice(asset)` → `LRTDepositPool.getRsETHAmountToMint()` (L519–520)
3. `getRsETHAmountToMint()` → `_beforeDeposit()` → `depositAsset()` / `depositETH()`

The minting formula at `LRTDepositPool.sol` L520 is:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

If `getAssetPrice(asset)` returns a stale inflated value while `rsETHPrice` reflects the true prior price, the depositor receives more rsETH than their deposit is worth. Additionally, `LRTOracle._getTotalEthInProtocol()` (L336–343) calls `getAssetPrice(asset)` for every supported asset to compute TVL, which feeds `_updateRsETHPrice()`. A stale inflated price here inflates `totalETHInProtocol`, causing excess fee rsETH to be minted to the treasury.

The `pricePercentageLimit` guard in `_updateRsETHPrice()` (L252–266) only applies to the price update path and only reverts for large deviations; it does not protect the deposit minting path, and small stale deviations (within the limit) pass through silently.

## Impact Explanation
**High — Theft of unclaimed yield.**

A depositor calling `depositAsset()` during a period when a supported LST asset's Chainlink feed is stale with an inflated last-known price receives more rsETH than their deposit is worth at the true current price. This excess rsETH dilutes the share value of all existing rsETH holders, constituting theft of unclaimed yield. The same stale price fed into `_updateRsETHPrice()` inflates `totalETHInProtocol`, causing excess fee rsETH to be minted to the treasury, further diluting holders.

## Likelihood Explanation
Chainlink LST/ETH feeds (stETH/ETH, cbETH/ETH, rETH/ETH) on mainnet have 24-hour heartbeat intervals. During periods of low volatility, network congestion, or oracle node downtime, a feed can remain at its last reported value for the full heartbeat window without triggering a deviation update. Any unprivileged depositor can call `depositAsset()` or `depositETH()` at any time with no special role, setup, or front-running required. The condition is passively reachable whenever a feed is stale.

## Recommendation
Apply the same validation pattern already used in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
(uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
    priceFeed.latestRoundData();

if (answeredInRound < roundId) revert StalePrice();
if (updatedAt == 0) revert IncompleteRound();
if (price <= 0) revert InvalidPrice();
// Optionally: if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();
```

## Proof of Concept

**Step 1 — Confirm vulnerable code:**
`ChainlinkPriceOracle.getAssetPrice()` discards all validation fields: [1](#0-0) 

**Step 2 — Confirm correct pattern exists in same codebase:**
`ChainlinkOracleForRSETHPoolCollateral.getRate()` validates all three conditions: [2](#0-1) 

**Step 3 — Stale price flows directly into rsETH mint calculation:** [3](#0-2) 

**Step 4 — Stale price also inflates TVL used for fee minting:** [4](#0-3) 

**Step 5 — Entry point is unprivileged:** [5](#0-4) 

**Foundry fork test plan:**
1. Fork mainnet at a block where a supported LST Chainlink feed (e.g., stETH/ETH) is within its heartbeat window.
2. Deploy a mock `AggregatorV3Interface` that returns a fixed stale `answer` with `updatedAt = block.timestamp - 25 hours` and `answeredInRound < roundId`.
3. Register the mock feed in `ChainlinkPriceOracle` via `updatePriceFeedFor`.
4. Call `depositAsset(stETH, amount, 0, "")` as an unprivileged address.
5. Assert that `rsethAmountToMint` exceeds the fair value computed using the true current price, confirming over-minting and share dilution for existing holders.

### Citations

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L52-54)
```text
        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L27-32)
```text
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();
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
