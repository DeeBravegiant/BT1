Audit Report

## Title
Missing Chainlink Price Validation Enables Stale/Invalid Price to Flow Into rsETH Mint Calculations - (File: contracts/oracles/ChainlinkPriceOracle.sol)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all four validation return values (`roundId`, `startedAt`, `updatedAt`, `answeredInRound`) and performs no positivity check on `price`. A stale or zero/negative price flows directly into `LRTDepositPool.getRsETHAmountToMint()`, allowing any depositor to mint excess rsETH at the expense of existing holders. The same codebase already applies correct validation in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, confirming the omission is unintentional.

## Finding Description
In `contracts/oracles/ChainlinkPriceOracle.sol` at line 52, `latestRoundData()` is called with all validation fields silently discarded:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
```

No check is made that:
- `price > 0` — a zero or negative `int256` cast to `uint256` wraps to a near-`type(uint256).max` value
- `updatedAt != 0` — guards against an incomplete round
- `answeredInRound >= roundId` — guards against a stale round
- `block.timestamp - updatedAt <= STALENESS_THRESHOLD` — guards against a heartbeat-expired price

By contrast, `contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol` lines 30–32 correctly enforces all three structural checks before returning a price:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
```

The stale price propagates through the following confirmed call chain:

1. `ChainlinkPriceOracle.getAssetPrice(asset)` → returns unvalidated price (line 52–54)
2. `LRTOracle.getAssetPrice(asset)` → delegates via `IPriceFetcher`
3. `LRTDepositPool.getRsETHAmountToMint()` → `rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice()` (line 520)
4. `LRTDepositPool._beforeDeposit()` → calls `getRsETHAmountToMint` (line 665)
5. `LRTDepositPool.depositAsset()` / `depositETH()` → callable by any unprivileged user (lines 99–118, 76–93)

## Impact Explanation
When a supported LST asset's Chainlink feed is stale (last reported price is higher than the true current price), any depositor calling `depositAsset()` receives rsETH computed against the inflated stale price. Since rsETH is a share token backed by the pool's total assets, the excess rsETH minted dilutes all existing holders' proportional claims on the pool. The value extracted by the attacker comes directly from existing rsETH holders' principal, constituting **direct theft of user funds** and, at sufficient scale, **protocol insolvency**. Additionally, if `price` is returned as a negative `int256`, the unchecked cast `uint256(price)` wraps to near-`type(uint256).max`, causing either a massive over-mint or an arithmetic revert.

**Impact level: Critical** — direct theft of existing rsETH holders' funds; potential protocol insolvency.

## Likelihood Explanation
Chainlink heartbeat intervals for LST/ETH feeds are commonly 24 hours. A price that is hours old can be returned without any on-chain indication of staleness, as long as the deviation threshold has not been breached. This scenario occurs during network congestion, sequencer downtime (on L2), or low-volatility periods where the deviation threshold is never triggered. The attacker requires no special privileges — only the ability to observe an on-chain price discrepancy between the stale Chainlink feed and the true market price, then call the public `depositAsset()` function. This is a well-documented real-world attack vector that has been exploited in other protocols.

## Recommendation
Apply the same validation pattern already present in `ChainlinkOracleForRSETHPoolCollateral.getRate()`, and additionally enforce a per-feed staleness threshold:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (block.timestamp - updatedAt > STALENESS_THRESHOLD) revert StalePrice();
    if (price <= 0) revert InvalidPrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

`STALENESS_THRESHOLD` should be set per-feed based on its documented heartbeat (e.g., 3600 seconds for a 1-hour heartbeat feed, 86 400 seconds for a 24-hour heartbeat feed).

## Proof of Concept

**Step 1 — Confirm vulnerable code path:**
`ChainlinkPriceOracle.getAssetPrice()` discards all validation fields and performs no sign check. [1](#0-0) 

**Step 2 — Confirm correct pattern exists in same repo:**
`ChainlinkOracleForRSETHPoolCollateral.getRate()` validates `answeredInRound`, `timestamp`, and `ethPrice`. [2](#0-1) 

**Step 3 — Confirm stale price flows into mint calculation:**
`getRsETHAmountToMint` uses the unvalidated price directly. [3](#0-2) 

**Step 4 — Confirm public entry point:**
`depositAsset()` is callable by any unprivileged user with no access control beyond `whenNotPaused`. [4](#0-3) 

**Foundry fork test plan:**
1. Fork mainnet at a block where a supported LST Chainlink feed has not updated for several hours.
2. Warp `block.timestamp` forward past the feed's heartbeat window without triggering a price update (simulate by using a mock `AggregatorV3Interface` that returns a fixed stale `updatedAt`).
3. Record the current rsETH price via `lrtOracle.rsETHPrice()`.
4. Call `depositAsset(lstToken, largeAmount, 0, "")` as an unprivileged attacker address.
5. Assert that `rsethAmountToMint` exceeds the fair value computed using the true current price.
6. Assert that existing rsETH holders' redemption value per share has decreased, confirming dilution/theft.

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
