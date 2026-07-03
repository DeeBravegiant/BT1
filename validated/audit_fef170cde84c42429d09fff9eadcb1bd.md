Audit Report

## Title
Stale Chainlink Price Accepted Without Freshness Validation in `ChainlinkPriceOracle.getAssetPrice()`, Enabling Over-Minting of rsETH on Deposit — (`contracts/oracles/ChainlinkPriceOracle.sol`)

## Summary
`ChainlinkPriceOracle.getAssetPrice()` calls `latestRoundData()` but discards all return values except `answer`, performing no staleness check (`updatedAt`), no incomplete-round check (`answeredInRound < roundId`), and no negative-price guard. This stale price flows directly into `LRTDepositPool.getRsETHAmountToMint()`, which computes rsETH minted per deposit. If a supported LST feed is stale at an inflated value, a depositor receives more rsETH than the deposited LST is worth, diluting the share value of all existing rsETH holders.

## Finding Description
`ChainlinkPriceOracle.getAssetPrice()` fetches the price as:

```solidity
(, int256 price,,,) = priceFeed.latestRoundData();
return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
``` [1](#0-0) 

All five return values of `latestRoundData()` — `roundId`, `answer`, `startedAt`, `updatedAt`, `answeredInRound` — are available, but only `answer` is used. There is no check that `answeredInRound >= roundId`, `updatedAt != 0`, `block.timestamp - updatedAt <= heartbeat`, or `price > 0`.

The same codebase already implements all of these checks in `ChainlinkOracleForRSETHPoolCollateral.getRate()`:

```solidity
if (answeredInRound < roundID) revert StalePrice();
if (timestamp == 0) revert IncompleteRound();
if (ethPrice <= 0) revert InvalidPrice();
``` [2](#0-1) 

`LRTOracle.getAssetPrice()` delegates directly to the registered `IPriceFetcher`, which for LST assets is `ChainlinkPriceOracle`: [3](#0-2) 

`LRTDepositPool.getRsETHAmountToMint()` uses this price to compute the rsETH amount minted: [4](#0-3) 

This result is consumed by the public, permissionless `depositAsset()`: [5](#0-4) 

No privileged access is required. Any caller can invoke `depositAsset()` during a stale oracle window.

## Impact Explanation
**High — Theft of unclaimed yield.**

When the Chainlink feed for a supported LST (e.g., stETH/ETH, rETH/ETH) is stale at a price higher than the LST's true current value (e.g., the LST has depegged or been slashed but the oracle has not yet updated), a depositor receives more rsETH than the deposited LST is worth. Because rsETH is a share token backed by the protocol's total ETH value, over-minting rsETH dilutes the share value for all existing holders — effectively transferring accrued yield and principal value from existing rsETH holders to the depositor. The magnitude scales with deposit size and the degree of price staleness.

## Likelihood Explanation
**Medium.** Chainlink feeds have documented heartbeat intervals (e.g., 1 hour for stETH/ETH on mainnet). During periods of network congestion, oracle keeper failures, or rapid LST price movement, the feed can lag behind the true price for the full heartbeat window. This is a known, historically observed condition. No privileged access is required — any depositor can exploit the window by calling the public `depositAsset()` function.

## Recommendation
Apply the same staleness and validity checks already present in `ChainlinkOracleForRSETHPoolCollateral` to `ChainlinkPriceOracle.getAssetPrice()`:

```solidity
function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
    AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

    (uint80 roundId, int256 price,, uint256 updatedAt, uint80 answeredInRound) =
        priceFeed.latestRoundData();

    if (answeredInRound < roundId) revert StalePrice();
    if (updatedAt == 0) revert IncompleteRound();
    if (price <= 0) revert InvalidPrice();
    // Optionally: if (block.timestamp - updatedAt > MAX_STALENESS) revert StalePrice();

    return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
}
```

## Proof of Concept
1. Chainlink stETH/ETH feed last updated at `T - 2h`; true stETH price has dropped 5% due to a slashing event, but the oracle still reports the pre-slash price (e.g., `1.05e18` instead of true `1.00e18`).
2. Attacker calls `LRTDepositPool.depositAsset(stETH, 1000e18, 0, "")`.
3. `getRsETHAmountToMint()` calls `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → returns stale inflated price.
4. `rsethAmountToMint = (1000e18 * 1.05e18) / rsETHPrice` — attacker receives ~5% more rsETH than the deposited stETH is worth.
5. Attacker holds or redeems rsETH, extracting value from existing holders. No admin action or special role is required.

**Foundry fork test plan:** Fork mainnet, mock the Chainlink stETH/ETH aggregator to return a `latestRoundData()` response where `updatedAt = block.timestamp - 2 hours` and `answeredInRound < roundId`. Call `depositAsset()` with a large stETH amount. Assert that `rsethAmountToMint` exceeds the fair value computed using the true current price, confirming over-minting and share dilution for existing holders.

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

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/LRTDepositPool.sol (L99-117)
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
```

**File:** contracts/LRTDepositPool.sol (L520-520)
```text
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
